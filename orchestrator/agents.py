from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
import socket
import time
from typing import Any
from urllib import error, request as urllib_request

from .config import RunConfig
from .env import load_project_env
from .serialization import SerializableDataclass
from .state import ActorRole, CoordinatorProposal, ExperimentProposal

DEFAULT_ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_ZHIPU_WORKER_MODEL = "glm-4.6v"
DEFAULT_ZHIPU_COORDINATOR_MODEL = "glm-4.7"
DEFAULT_ZHIPU_MODEL = DEFAULT_ZHIPU_WORKER_MODEL
DEFAULT_COMPLETIONS_PATH = "/chat/completions"
DEFAULT_WORKER_PROMPT_PROFILE = "maar_wide"
DEFAULT_SPECIALIST_PROMPT_PROFILE = "agent_groupchat_specialist"
DEFAULT_ENGINEER_PROMPT_PROFILE = "agent_groupchat_engineer"
DEFAULT_COORDINATOR_PROMPT_PROFILE = "coordinator"
AUTORESEARCH_ORIGINAL_PROMPT_PROFILE = "autoresearch_original"


class AgentError(RuntimeError):
    """Raised when an agent backend cannot produce a valid proposal."""


@dataclass(slots=True)
class ProposalRequest(SerializableDataclass):
    actor_role: ActorRole
    actor_id: str
    round_id: int
    baseline_commit: str
    workspace_path: Path
    artifact_dir: Path | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.workspace_path = Path(self.workspace_path).expanduser().resolve()
        if self.artifact_dir is not None:
            self.artifact_dir = Path(self.artifact_dir).expanduser().resolve()


class AgentRunner(ABC):
    """Interface for worker/coordinator proposal generation."""

    @abstractmethod
    def propose(self, request: ProposalRequest) -> ExperimentProposal:
        raise NotImplementedError


class ReplayAgentRunner(AgentRunner):
    """Deterministic proposal source used for tests and local dry runs."""

    def __init__(self, proposals_by_actor: dict[str, list[ExperimentProposal | Exception]]):
        self._proposals_by_actor = {key: list(value) for key, value in proposals_by_actor.items()}

    def propose(self, request: ProposalRequest) -> ExperimentProposal:
        queue = self._proposals_by_actor.get(request.actor_id)
        if not queue:
            raise AgentError(f"no replay proposal configured for {request.actor_id}")
        next_item = queue.pop(0)
        if isinstance(next_item, Exception):
            raise AgentError(str(next_item)) from next_item
        return next_item


class OpenAICompatibleChatClient:
    """Minimal chat-completions client for OpenAI-compatible providers."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model_name: str,
        timeout_seconds: float = 60.0,
        transport: Any | None = None,
    ):
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name.strip()
        self.timeout_seconds = float(timeout_seconds)
        self.transport = transport or self._default_transport

        if not self.api_key:
            raise ValueError("api_key must not be empty")
        if not self.base_url:
            raise ValueError("base_url must not be empty")
        if not self.model_name:
            raise ValueError("model_name must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")

    @property
    def endpoint_url(self) -> str:
        return f"{self.base_url}{DEFAULT_COMPLETIONS_PATH}"

    def create_chat_completion(
        self,
        *,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
        response_format: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        return self.transport(self.endpoint_url, self.api_key, payload, self.timeout_seconds)

    def _default_transport(
        self,
        url: str,
        api_key: str,
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        req = urllib_request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        opener = urllib_request.build_opener(urllib_request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise AgentError(f"chat completion failed with HTTP {exc.code}: {body}") from exc
        except error.URLError as exc:
            raise AgentError(f"chat completion request failed: {exc.reason}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise AgentError(f"chat completion request timed out: {exc}") from exc
        try:
            decoded = json.loads(body)
        except json.JSONDecodeError as exc:
            raise AgentError("chat completion returned non-JSON response") from exc
        return decoded


class ZhipuChatAgentRunner(AgentRunner):
    """Real GLM-backed agent runner using the official chat-completions API."""

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str = DEFAULT_ZHIPU_MODEL,
        base_url: str = DEFAULT_ZHIPU_BASE_URL,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        prompt_profile: str = DEFAULT_WORKER_PROMPT_PROFILE,
        transport: Any | None = None,
        sleep_func: Any | None = None,
    ):
        self.client = OpenAICompatibleChatClient(
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )
        self.max_retries = int(max_retries)
        self.prompt_profile = prompt_profile.strip() or DEFAULT_WORKER_PROMPT_PROFILE
        self.sleep_func = sleep_func or time.sleep
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")

    @classmethod
    def from_env(
        cls,
        *,
        model_name: str = "",
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        prompt_profile: str = DEFAULT_WORKER_PROMPT_PROFILE,
        project_root: Path | None = None,
        transport: Any | None = None,
    ) -> ZhipuChatAgentRunner:
        load_project_env(project_root)
        api_key = os.environ.get("ZHIPUAI_API_KEY", "").strip()
        if not api_key:
            raise AgentError("ZHIPUAI_API_KEY is not set")
        resolved_model = model_name.strip() or os.environ.get("ZHIPUAI_MODEL", DEFAULT_ZHIPU_MODEL).strip()
        resolved_base_url = os.environ.get("ZHIPUAI_BASE_URL", DEFAULT_ZHIPU_BASE_URL).strip()
        return cls(
            api_key=api_key,
            model_name=resolved_model or DEFAULT_ZHIPU_MODEL,
            base_url=resolved_base_url or DEFAULT_ZHIPU_BASE_URL,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            prompt_profile=prompt_profile,
            transport=transport,
        )

    def propose(self, request: ProposalRequest) -> ExperimentProposal:
        editable_path = request.workspace_path / "train.py"
        if not editable_path.exists():
            raise AgentError(f"editable file does not exist: {editable_path}")

        train_text = editable_path.read_text(encoding="utf-8")
        messages = self._build_messages(request, train_text)
        self._write_request_log(request, messages)

        last_error = "proposal generation failed"
        total_attempts = self.max_retries + 1
        for attempt in range(1, self.max_retries + 2):
            try:
                self._log_progress(request, f"agent attempt {attempt}/{total_attempts} started for {request.actor_id}")
                response_payload = self.client.create_chat_completion(
                    messages=messages,
                    temperature=0.2,
                    response_format={"type": "json_object"},
                )
                self._write_response_log(request, attempt, response_payload)
                raw_content = self._extract_message_content(response_payload)
                self._write_output_log(request, attempt, raw_content)
                parsed_payload = self._parse_json_object(raw_content)
                self._log_progress(request, f"agent attempt {attempt}/{total_attempts} succeeded for {request.actor_id}")
                return self._build_proposal(request.actor_role, parsed_payload)
            except (AgentError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                self._write_error_log(request, attempt, last_error)
                if attempt >= total_attempts:
                    break
                delay = self._retry_delay_seconds(last_error, attempt)
                if delay > 0:
                    self._log_progress(
                        request,
                        f"agent attempt {attempt}/{total_attempts} failed for {request.actor_id}: {last_error}; retrying in {delay:.1f}s",
                    )
                    self.sleep_func(delay)
                else:
                    self._log_progress(
                        request,
                        f"agent attempt {attempt}/{total_attempts} failed for {request.actor_id}: {last_error}; retrying immediately",
                    )

        raise AgentError(last_error)

    def _retry_delay_seconds(self, error_message: str, attempt: int) -> float:
        lowered = error_message.lower()
        if "http 429" in lowered or "\"code\":\"1302\"" in lowered or "rate limit" in lowered:
            return min(30.0, 5.0 * attempt)
        if "timed out" in lowered or "timeout" in lowered or "temporarily unavailable" in lowered:
            return min(20.0, 3.0 * attempt)
        if "request failed" in lowered:
            return min(10.0, 2.0 * attempt)
        return 0.0

    def _build_messages(self, request: ProposalRequest, train_text: str) -> list[dict[str, str]]:
        system_prompt = self._system_prompt(request)
        user_prompt = self._user_prompt(request, train_text)
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _system_prompt(self, request: ProposalRequest) -> str:
        if request.actor_role is ActorRole.COORDINATOR:
            return self._coordinator_system_prompt()
        if self.prompt_profile == DEFAULT_ENGINEER_PROMPT_PROFILE and request.actor_role is ActorRole.ENGINEER:
            return self._agent_groupchat_engineer_system_prompt()
        if self.prompt_profile == AUTORESEARCH_ORIGINAL_PROMPT_PROFILE:
            return self._autoresearch_original_worker_system_prompt()
        if self.prompt_profile == DEFAULT_SPECIALIST_PROMPT_PROFILE and request.actor_role is ActorRole.SPECIALIST:
            return self._agent_groupchat_specialist_system_prompt(request)
        return self._maar_worker_system_prompt()

    def _maar_worker_system_prompt(self) -> str:
        rules = [
            "You are improving autoresearch/train.py inside a controlled experiment loop.",
            "Think like an autonomous researcher trying to advance the current mainline, not like a generic patch generator.",
            "Your goal is to propose one coherent experiment that could lower val_bpb under the fixed training budget.",
            "Use the shared experience notes as evidence. Avoid repeating recent failed directions, but do not let the notes collapse your search to one mechanism.",
            "Treat positive directions as evidence, not as an instruction to keep optimizing the same mechanism.",
            "If one family of ideas has already improved multiple times, prefer exploring a different mechanism unless you have a concrete reason to stay with that family.",
            "For smaller GPUs and fixed short budgets, favor directions that can improve learning efficiency early.",
            "Within train.py, broad exploration is allowed: model shape, depth or width, head layout, attention patterns, activations and MLP design, residual and value-embedding logic, optimizer settings, schedule logic, batch geometry, and other training logic.",
            "Keep proposals within a plausible 3090 memory budget; avoid obvious width, depth, or batch explosions that are likely to OOM.",
            "Treat depth inflation, embedding inflation, shape-changing optimizer interactions, and norm or attention kernel path swaps as high-risk moves unless your hypothesis explicitly addresses memory and kernel compatibility.",
            "One strong experimental hypothesis is better than several unrelated tweaks.",
            "All else being equal, simpler is better, but meaningful improvements are worth moderate complexity.",
            "You may only propose a single Search/Replace edit against train.py.",
            "Return JSON only. Do not wrap the JSON in markdown.",
            "Return a very short idea_summary that describes the optimization direction only, not the code.",
            "search_block must be copied verbatim from the current train.py content.",
            "search_block must be specific enough to match exactly once after insertion.",
            "replace_block must be the full replacement snippet.",
            "replace_block must be valid Python when inserted verbatim into train.py.",
            "Preserve indentation exactly, including leading spaces, in both search_block and replace_block.",
            "Keep the change syntactically valid and targeted at improving val_bpb.",
            "Do not propose logging-only, timestamp-only, comment-only, or formatting-only changes.",
            "Do not add traceability, debug output, or cosmetic edits unless they are required for the optimization itself.",
            "Do not introduce break, continue, or return statements unless the search_block already contains the same control-flow statement in the same scope.",
            "Do not move loop-only or function-only statements outside their original nesting level.",
            "You may change existing hyperparameters, optimizer logic, architecture code, or a full existing function body when the hypothesis clearly requires it.",
            "Replacing an existing helper or function body is allowed when the experiment requires it.",
            "Do not rewrite, duplicate, or relocate the entire main training loop.",
            "Do not add new checkpoint files, torch.save, torch.load, open(...), exit(...), or any other new filesystem or process side effects.",
            "Do not assume a later checkpoint or eval step will exist; any new state must be safe even if an optional branch never runs.",
            "Do not introduce new nested training passes or duplicated evaluation or training pipelines.",
            "Do not modify prepare.py, tokenizer setup, evaluation harness, file paths, cache paths, device placement, or distributed/runtime bootstrap code.",
            "Return the fields motivation, search_block, and replace_block.",
        ]
        return "\n".join(rules)

    def _autoresearch_original_worker_system_prompt(self) -> str:
        rules = [
            "This is an experiment to have the LLM do its own research.",
            "You are operating inside the autoresearch experiment loop.",
            "Each experiment runs on a single GPU and the training script runs for a fixed time budget of 5 minutes.",
            "What you CAN do: modify train.py. Everything in train.py is fair game: model architecture, optimizer, hyperparameters, training loop, batch size, model size, and other training code.",
            "What you CANNOT do: modify prepare.py, add new dependencies, or modify the evaluation harness.",
            "The goal is simple: get the lowest val_bpb.",
            "VRAM is a soft constraint. Some increase is acceptable for meaningful val_bpb gains, but it should not blow up dramatically.",
            "Simplicity criterion: all else being equal, simpler is better. A small improvement that adds ugly complexity is not worth it. Removing something and getting equal or better results is a great outcome.",
            "You are choosing the next single experiment to try on the current mainline.",
            "Return that experiment as one Search/Replace edit against train.py.",
            "Return JSON only. Do not wrap the JSON in markdown.",
            "Return a very short idea_summary that describes the optimization direction only, not the code.",
            "search_block must be copied verbatim from the current train.py content.",
            "replace_block must be the full replacement snippet.",
            "replace_block must be valid Python when inserted verbatim into train.py.",
            "Preserve indentation exactly, including leading spaces, in both search_block and replace_block.",
            "You may freely modify train.py, including model structure, optimizer logic, hyperparameters, schedules, batch size, model size, and training logic.",
            "Keep the code runnable and within a plausible single-3090 VRAM budget; avoid obvious width, depth, or batch explosions that are likely to OOM.",
            "Do not add new packages, files, subprocesses, or filesystem side effects such as torch.save, torch.load, open(...), or exit(...).",
            "Do not modify prepare.py, tokenizer setup, or evaluate_bpb.",
            "Return the fields motivation, search_block, and replace_block.",
        ]
        return "\n".join(rules)

    def _coordinator_system_prompt(self) -> str:
        rules = [
            "You are improving autoresearch/train.py inside a controlled experiment loop.",
            "You are merging ideas from multiple successful worker candidates.",
            "Act as a curator: extract the strongest lesson and only propose a merge when there is real composition value.",
            "Use worker results as evidence, but avoid synthesizing a large speculative algorithm.",
            "If candidates touch the same exact code region or represent the same mechanism, treat them as competing alternatives rather than something to average together.",
            "When workers touch disjoint regions and both are supported by evidence, prefer a simple composition of those validated edits.",
            "When one worker is clearly stronger and there is no real composition value, copy the strongest candidate exactly instead of inventing a midpoint.",
            "The merged proposal must still use a verbatim search_block copied from the current train.py.",
            "Prefer the smallest merged edit that preserves the best validated idea or composition of validated ideas.",
            "Return JSON only. Do not wrap the JSON in markdown.",
            "Return a very short idea_summary that describes the optimization direction only, not the code.",
            "Return a short curator_note that captures the high-level lesson from this merge attempt without mentioning code, and avoid overfitting the note to one mechanism when the round suggests diversification.",
            "search_block must be copied verbatim from the current train.py content.",
            "search_block must be specific enough to match exactly once after insertion.",
            "replace_block must be the full replacement snippet.",
            "replace_block must be valid Python when inserted verbatim into train.py.",
            "Preserve indentation exactly, including leading spaces, in both search_block and replace_block.",
            "Do not add new files, subprocesses, or filesystem side effects.",
            "Do not modify prepare.py, tokenizer setup, evaluation harness, file paths, cache paths, device placement, or distributed/runtime bootstrap code.",
            "Include merge_rationale and source_candidates in the JSON response.",
        ]
        return "\n".join(rules)

    def _agent_groupchat_specialist_system_prompt(self, request: ProposalRequest) -> str:
        specialist_role = str(request.context.get("specialist_role", request.actor_id)).strip() or request.actor_id
        common_rules = [
            "You are one specialist inside an agent_groupchat relay that is refining a single shared candidate before training.",
            "Treat the current train.py as a shared draft, not a private branch.",
            "You can see the full train.py file for context, but your actual edit should stay centered on your assigned responsibility unless a small integration fix is necessary.",
            "You have a primary specialty, but the division is soft: you may cross boundaries when integration requires it.",
            "Build on the current shared candidate and the explicit groupchat memory. Do not ignore accepted turns just because they were made by another specialist.",
            "Treat the accepted_turns in the context block as the explicit group chat history. Use them to understand what earlier specialists intended, then complement, tighten, or locally revise those ideas instead of acting like an isolated agent.",
            "Success means lower val_bpb under the active short-budget benchmark. The relevant budget is short, currently 300s or 600s and never longer than 600s.",
            "Favor changes that pay off early within that budget. Do not optimize for long-run elegance, slower-burn convergence, or generic modernity if those do not help within the active short-budget run.",
            "Prefer one coherent incremental improvement that strengthens or repairs the current shared candidate.",
            "You may modify model structure, optimizer logic, schedules, memory or efficiency logic, and related training code inside train.py.",
            "Keep the proposal within a plausible 3090 memory budget and avoid obvious width, depth, head-count, embedding, or batch explosions.",
            "Do not stack multiple unrelated ideas into one patch. One narrow, testable change is better than a bundle of speculative edits.",
            "Do not change a shared module interface, tensor geometry contract, or container type unless your single patch also updates every directly dependent initialization, forward, and access path in the same edit.",
            "Avoid converting a single module into Sequential, ModuleDict, ModuleList, or another container unless that conversion is essential and fully coherent in one patch.",
            "Treat shared components such as value_embeds, ve_gate, normalization helpers, attention projections, and other reused module interfaces as high-risk coordination surfaces; do not change their representation or access pattern unless you update every direct call site in the same patch.",
            "Avoid introducing edits that are likely to create missing attributes, API mismatches, or parameters that receive no gradient.",
            "If you revise an earlier specialist's change, do it explicitly and locally instead of rewriting the whole training system.",
            "Return JSON only. Do not wrap the JSON in markdown.",
            "Return a short idea_summary describing the optimization direction only, not the code.",
            "You may only propose a single Search/Replace edit against train.py.",
            "search_block must be copied verbatim from the current train.py content.",
            "search_block must be specific enough to match exactly once.",
            "replace_block must be the full replacement snippet and remain valid Python when inserted verbatim.",
            "Preserve indentation exactly, including leading spaces, in both search_block and replace_block.",
            "Do not add new files, subprocesses, or filesystem side effects such as torch.save, torch.load, open(...), or exit(...).",
            "Do not modify prepare.py, tokenizer setup, evaluation harness, cache paths, file paths, device placement, or runtime bootstrap code.",
            "Return the fields motivation, search_block, and replace_block.",
        ]
        role_guidance = {
            "architecture": [
                "Your primary role is architecture.",
                "Prefer changes to attention, MLP, residual paths, normalization, embeddings, and structural layout.",
                "Only make architecture changes when you have a concrete short-budget hypothesis for why they should improve 300s or 600s performance.",
                "Do not default to standardization or more modern blocks just because they are fashionable; they must earn their cost inside the short benchmark.",
                "If you change structure, preserve downstream initialization and call-site contracts unless you also repair them in the same patch.",
                "Do not drift into schedule-only tuning unless it is needed to make the shared structural direction coherent.",
            ],
            "optimizer_schedule": [
                "Your primary role is optimizer_schedule.",
                "Prefer changes to optimizer settings, warmup or warmdown, decay logic, batch geometry, gradient accumulation, and training dynamics.",
                "Favor schedule and optimizer changes that improve early learning under the active 300s or 600s budget rather than slower long-horizon behavior.",
                "You may touch structure only when needed to make the current shared candidate trainable or better coupled to its schedule, and those structural touches should stay minimal.",
            ],
            "efficiency_memory": [
                "Your primary role is efficiency_memory.",
                "Prefer changes that improve short-budget learning efficiency, memory headroom, throughput, and benchmark fit.",
                "Treat memory savings as useful only when they plausibly improve the short-budget result; avoid changes that merely look cleaner without helping early training.",
                "Watch for high-risk structure inflation, kernel incompatibility, optimizer-shape interactions, and container or API mismatches when integrating earlier turns.",
            ],
        }
        return "\n".join(common_rules + role_guidance.get(specialist_role, []))

    def _agent_groupchat_engineer_system_prompt(self) -> str:
        rules = [
            "You are the engineer fallback inside an agent_groupchat relay.",
            "A shared candidate has already been assembled by specialists and then crashed during the final training run.",
            "Your job is to debug the current shared candidate while preserving as much of the specialists' work and intended direction as possible.",
            "Prefer the smallest local repair that makes the current candidate runnable again.",
            "Treat the crash log as primary evidence. Fix the actual failure rather than replacing the whole idea with a different experiment.",
            "Do not restart the design from scratch. Do not rewrite the training system. Do not discard accepted specialist changes unless they directly cause the crash.",
            "You may modify model structure, optimizer logic, shape handling, initialization, schedule logic, and related train.py code when needed to repair the failure.",
            "Hard red lines: never keep or introduce a new module attribute read unless the attribute is fully defined in __init__ and the surrounding path stays internally consistent.",
            "Hard red lines: if a specialist partially converts an MLP or gated block, either complete every companion layer consistently or revert only the broken fragment; do not leave half-converted gate paths behind.",
            "Hard red lines: do not change value_embeds dimensionality, n_head or n_kv_head relationships, head_dim assumptions, or attention/value tensor view or reshape geometry unless the crash log explicitly points to that exact geometry and your single patch updates every dependent path coherently.",
            "Hard red lines: if a specialist turns a Linear or other single module into Sequential, ModuleList, ModuleDict, or another container, you must also keep initialization and every direct .weight or .bias access consistent, or revert that containerization.",
            "Hard red lines: when the crash indicates a shared interface mismatch, search for the same symbol across init_weights, estimate_flops, forward, and other direct access paths, then repair that whole family in one patch instead of fixing only the first failing line.",
            "Hard red lines: do not keep or introduce parameters that are likely to receive no gradient under the current forward path; prefer reverting the smallest broken subchange over leaving partially wired modules for Muon or AdamW to discover later.",
            "Hard red lines: do not fix one crash by stacking a second speculative architecture change, widening tensors, or changing unrelated model geometry.",
            "Prefer reverting the smallest broken subchange over extending a partially broken architectural edit.",
            "You may only propose a single Search/Replace edit against train.py.",
            "Return JSON only. Do not wrap the JSON in markdown.",
            "Return a short idea_summary describing the repair direction only, not the code.",
            "search_block must be copied verbatim from the current train.py content.",
            "search_block must be specific enough to match exactly once.",
            "replace_block must be the full replacement snippet and remain valid Python when inserted verbatim.",
            "Preserve indentation exactly, including leading spaces, in both search_block and replace_block.",
            "Do not add new files, subprocesses, or filesystem side effects such as torch.save, torch.load, open(...), or exit(...).",
            "Do not modify prepare.py, tokenizer setup, evaluation harness, cache paths, file paths, device placement, or runtime bootstrap code.",
            "Return the fields motivation, search_block, and replace_block.",
        ]
        return "\n".join(rules)

    def _user_prompt(self, request: ProposalRequest, train_text: str) -> str:
        if request.actor_role is ActorRole.COORDINATOR:
            output_contract = {
                "motivation": "short explanation",
                "idea_summary": "one-sentence direction only, no code",
                "merge_rationale": "why the merged change should outperform the individual workers",
                "curator_note": "one-sentence lesson only, no code",
                "source_candidates": ["worker-1", "worker-2"],
                "search_block": "exact snippet from train.py",
                "replace_block": "replacement snippet",
            }
            context_text = json.dumps(request.context, indent=2, sort_keys=True, ensure_ascii=False)
            return (
                f"Actor role: {request.actor_role.value}\n"
                f"Actor id: {request.actor_id}\n"
                f"Round id: {request.round_id}\n"
                f"Baseline commit: {request.baseline_commit}\n\n"
                "Editable file path: train.py\n"
                "Current train.py content:\n"
                "```python\n"
                f"{train_text}\n"
                "```\n\n"
                "Important formatting rule: preserve all leading spaces from the original file in search_block and replace_block.\n\n"
                "Additional orchestrator context:\n"
                "```json\n"
                f"{context_text}\n"
                "```\n\n"
                "Return exactly one JSON object that matches this contract:\n"
                "```json\n"
                f"{json.dumps(output_contract, indent=2, ensure_ascii=False)}\n"
                "```"
            )
        else:
            output_contract = {
                "motivation": "short explanation",
                "idea_summary": "one-sentence direction only, no code",
                "search_block": "exact snippet from train.py",
                "replace_block": "replacement snippet",
            }

        if self.prompt_profile == AUTORESEARCH_ORIGINAL_PROMPT_PROFILE:
            return (
                f"Actor role: {request.actor_role.value}\n"
                f"Actor id: {request.actor_id}\n"
                f"Round id: {request.round_id}\n"
                f"Baseline commit: {request.baseline_commit}\n\n"
                "This is one iteration of the autoresearch loop. If the experiment works, the branch will advance. If it does not, it will be discarded.\n\n"
                "Editable file path: train.py\n"
                "Current train.py content:\n"
                "```python\n"
                f"{train_text}\n"
                "```\n\n"
                "Important formatting rule: preserve all leading spaces from the original file in search_block and replace_block.\n\n"
                "Return exactly one JSON object that matches this contract:\n"
                "```json\n"
                f"{json.dumps(output_contract, indent=2, ensure_ascii=False)}\n"
                "```"
            )

        context_text = json.dumps(request.context, indent=2, sort_keys=True, ensure_ascii=False)
        return (
            f"Actor role: {request.actor_role.value}\n"
            f"Actor id: {request.actor_id}\n"
            f"Round id: {request.round_id}\n"
            f"Baseline commit: {request.baseline_commit}\n\n"
            "Treat this as one research iteration on the current mainline.\n"
            "Read the shared experience notes in the context block before choosing a direction.\n"
            "Avoid exact repeats of recent negative directions unless your motivation gives a concrete counter-hypothesis.\n"
            "Do not let repeated positive directions trap you in the same mechanism; explore a different family when the notes suggest one family is already over-represented.\n\n"
            "Editable file path: train.py\n"
            "Current train.py content:\n"
            "```python\n"
            f"{train_text}\n"
            "```\n\n"
            "Important formatting rule: preserve all leading spaces from the original file in search_block and replace_block.\n\n"
            "Additional orchestrator context:\n"
            "```json\n"
            f"{context_text}\n"
            "```\n\n"
            "Merge guidance:\n"
            "- Prefer a true composition only when the candidates touch different code regions or clearly compatible mechanisms.\n"
            "- If the candidates modify the same exact search_block, do not invent a compromise unless there is very strong evidence; usually you should keep the strongest worker edit exactly.\n"
            "- Use the per-candidate metrics and idea_family fields in the context block as evidence.\n\n"
            "Return exactly one JSON object that matches this contract:\n"
            "```json\n"
            f"{json.dumps(output_contract, indent=2, ensure_ascii=False)}\n"
            "```"
        )

    def _extract_message_content(self, response_payload: dict[str, Any]) -> str:
        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AgentError("chat completion response missing choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise AgentError("chat completion response missing message")
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text:
                        chunks.append(str(text))
            joined = "".join(chunks).strip()
            if joined:
                return joined
        raise AgentError("chat completion response content was empty")

    def _parse_json_object(self, raw_content: str) -> dict[str, Any]:
        text = raw_content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and start < end:
                text = text[start:end + 1]

        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise AgentError("chat completion did not return a JSON object")
        return parsed

    def _build_proposal(self, actor_role: ActorRole, payload: dict[str, Any]) -> ExperimentProposal:
        motivation = self._require_text(payload, "motivation")
        idea_summary = self._normalize_summary(payload.get("idea_summary"), "idea_summary")
        search_block = self._require_text(payload, "search_block")
        replace_block = self._require_text(payload, "replace_block")

        if actor_role is ActorRole.COORDINATOR:
            source_candidates_raw = payload.get("source_candidates", [])
            source_candidates: list[str]
            if isinstance(source_candidates_raw, list):
                source_candidates = [str(item).strip() for item in source_candidates_raw if str(item).strip()]
            else:
                raise AgentError("source_candidates must be a list when present")
            return CoordinatorProposal(
                motivation=motivation,
                idea_summary=idea_summary,
                merge_rationale=str(payload.get("merge_rationale", "")).strip(),
                source_candidates=source_candidates,
                curator_note=self._normalize_optional_summary(payload.get("curator_note", "")),
                search_block=search_block,
                replace_block=replace_block,
            )

        return ExperimentProposal(
            motivation=motivation,
            idea_summary=idea_summary,
            search_block=search_block,
            replace_block=replace_block,
        )

    def _require_text(self, payload: dict[str, Any], field_name: str) -> str:
        value = payload.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise AgentError(f"{field_name} must be a non-empty string")
        return value.strip()

    def _normalize_summary(self, value: Any, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise AgentError(f"{field_name} must be a non-empty string")
        normalized = " ".join(value.split()).strip()
        if "```" in normalized:
            raise AgentError(f"{field_name} must not contain code fences")
        if len(normalized) > 240:
            raise AgentError(f"{field_name} must be <= 240 characters")
        return normalized

    def _normalize_optional_summary(self, value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise AgentError("curator_note must be a string when present")
        normalized = " ".join(value.split()).strip()
        if not normalized:
            return ""
        if "```" in normalized:
            raise AgentError("curator_note must not contain code fences")
        if len(normalized) > 240:
            raise AgentError("curator_note must be <= 240 characters")
        return normalized

    def _write_request_log(self, request: ProposalRequest, messages: list[dict[str, str]]) -> None:
        if request.artifact_dir is None:
            return
        payload = {
            "backend": "zhipu",
            "model": self.client.model_name,
            "endpoint_url": self.client.endpoint_url,
            "actor_role": request.actor_role.value,
            "actor_id": request.actor_id,
            "round_id": request.round_id,
            "baseline_commit": request.baseline_commit,
            "messages": messages,
        }
        self._write_json(request.artifact_dir / "agent_request.json", payload)

    def _write_response_log(self, request: ProposalRequest, attempt: int, payload: dict[str, Any]) -> None:
        if request.artifact_dir is None:
            return
        self._write_json(request.artifact_dir / f"agent_response_{attempt:02d}.json", payload)

    def _write_output_log(self, request: ProposalRequest, attempt: int, raw_content: str) -> None:
        if request.artifact_dir is None:
            return
        path = request.artifact_dir / f"agent_output_{attempt:02d}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw_content + "\n", encoding="utf-8")

    def _write_error_log(self, request: ProposalRequest, attempt: int, message: str) -> None:
        if request.artifact_dir is None:
            return
        path = request.artifact_dir / f"agent_error_{attempt:02d}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(message + "\n", encoding="utf-8")

    def _log_progress(self, request: ProposalRequest, message: str) -> None:
        print(f"[zhipu:{self.client.model_name}] {message}", flush=True)

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def build_agent_runner(
    config: RunConfig,
    actor_role: ActorRole,
    *,
    project_root: Path | None = None,
    transport: Any | None = None,
) -> AgentRunner:
    """Create a real agent backend from RunConfig."""

    if actor_role in {ActorRole.WORKER, ActorRole.SPECIALIST, ActorRole.ENGINEER}:
        backend = config.worker_agent_backend.strip().lower()
        if actor_role is ActorRole.SPECIALIST:
            model_name = config.agent_groupchat.specialist_model_name
            prompt_profile = config.agent_groupchat.specialist_prompt_profile or DEFAULT_SPECIALIST_PROMPT_PROFILE
        elif actor_role is ActorRole.ENGINEER:
            model_name = config.agent_groupchat.engineer_model_name
            prompt_profile = config.agent_groupchat.engineer_prompt_profile or DEFAULT_ENGINEER_PROMPT_PROFILE
        else:
            model_name = _resolve_zhipu_model_name(
                actor_role,
                explicit_model_name=config.worker_model_name,
                project_root=project_root,
            )
            prompt_profile = config.worker_prompt_profile or DEFAULT_WORKER_PROMPT_PROFILE
    else:
        backend = config.coordinator_agent_backend.strip().lower()
        model_name = _resolve_zhipu_model_name(
            actor_role,
            explicit_model_name=config.coordinator_model_name,
            project_root=project_root,
        )
        prompt_profile = config.coordinator_prompt_profile or DEFAULT_COORDINATOR_PROMPT_PROFILE

    if backend in {"mock", "replay"}:
        raise ValueError(f"backend {backend!r} requires explicit runner construction")
    if backend in {"glm", "zhipu", "zhipuai"}:
        return ZhipuChatAgentRunner.from_env(
            model_name=model_name,
            timeout_seconds=config.agent_timeout_seconds,
            max_retries=config.agent_max_retries,
            prompt_profile=prompt_profile,
            project_root=project_root,
            transport=transport,
        )
    raise ValueError(f"unsupported agent backend: {backend}")


def _resolve_zhipu_model_name(
    actor_role: ActorRole,
    *,
    explicit_model_name: str,
    project_root: Path | None,
) -> str:
    resolved_explicit = explicit_model_name.strip()
    if resolved_explicit:
        return resolved_explicit

    load_project_env(project_root)
    if actor_role is ActorRole.COORDINATOR:
        return (
            os.environ.get("ZHIPUAI_COORDINATOR_MODEL", "").strip()
            or os.environ.get("ZHIPUAI_MODEL", DEFAULT_ZHIPU_COORDINATOR_MODEL).strip()
            or DEFAULT_ZHIPU_COORDINATOR_MODEL
        )
    return (
        os.environ.get("ZHIPUAI_WORKER_MODEL", "").strip()
        or os.environ.get("ZHIPUAI_MODEL", DEFAULT_ZHIPU_WORKER_MODEL).strip()
        or DEFAULT_ZHIPU_WORKER_MODEL
    )
