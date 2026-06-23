from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .schemas import SchemaError
from .tracing import TraceLogger, summarize_text, text_hash


class ModelConfigError(RuntimeError):
    """Raised when a configured model tier cannot be called."""


class ModelCallError(RuntimeError):
    """Raised when a model request fails."""


@dataclass(frozen=True)
class ModelTier:
    name: str
    provider: str
    base_url_env: str
    api_key_env: str
    configured_base_url: str
    configured_api_key: str
    model: str
    reasoning_effort: str

    @property
    def base_url(self) -> str:
        return self.configured_base_url.strip() or os.environ.get(self.base_url_env, "").strip()

    @property
    def api_key(self) -> str:
        return self.configured_api_key.strip() or os.environ.get(self.api_key_env, "").strip()

    @property
    def base_url_source(self) -> str:
        if self.configured_base_url.strip():
            return "config"
        if os.environ.get(self.base_url_env, "").strip():
            return "env"
        return "missing"

    @property
    def api_key_source(self) -> str:
        if self.configured_api_key.strip():
            return "config"
        if os.environ.get(self.api_key_env, "").strip():
            return "env"
        if self.base_url.startswith("mock://"):
            return "mock"
        return "missing"

    @property
    def ready(self) -> bool:
        if self.base_url.startswith("mock://"):
            return True
        return bool(self.base_url and self.api_key)


@dataclass
class ModelResponse:
    text: str
    raw: dict[str, Any]
    usage: dict[str, Any]


def get_model_tier(config: dict[str, Any], tier_name: str) -> ModelTier:
    tiers = config.get("model_tiers", {})
    tier = tiers.get(tier_name)
    if not isinstance(tier, dict):
        raise ModelConfigError(f"model tier not configured: {tier_name}")
    return ModelTier(
        name=tier_name,
        provider=str(tier.get("provider", "gpt_compatible")),
        base_url_env=str(tier.get("base_url_env", "GPT_API_BASE_URL")),
        api_key_env=str(tier.get("api_key_env", "GPT_API_KEY")),
        configured_base_url=str(tier.get("base_url", "") or ""),
        configured_api_key=str(tier.get("api_key", "") or ""),
        model=str(tier.get("model", "")),
        reasoning_effort=str(tier.get("reasoning_effort", "standard")),
    )


def doctor(config: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for tier_name in ("cheap", "standard", "strong", "frontier"):
        tier = get_model_tier(config, tier_name)
        rows.append(
            {
                "tier": tier.name,
                "provider": tier.provider,
                "model": tier.model,
                "reasoning_effort": tier.reasoning_effort,
                "base_url_env": tier.base_url_env,
                "api_key_env": tier.api_key_env,
                "base_url_source": tier.base_url_source,
                "api_key_source": tier.api_key_source,
                "base_url_set": bool(tier.base_url),
                "api_key_set": bool(tier.api_key) or tier.base_url.startswith("mock://"),
                "ready": tier.ready,
            }
        )
    return {
        "ready": all(row["ready"] for row in rows),
        "tiers": rows,
        "notes": [
            "ChatGPT Plus subscription is not used here; this CLI expects a GPT-compatible API relay.",
            "Configure credentials in project secrets.local.yaml, config.yaml, or environment variables.",
            "Keys are never printed by doctor or written to traces.",
        ],
    }


class GPTCompatibleClient:
    def __init__(self, tier: ModelTier, *, timeout: float = 90.0):
        if tier.provider != "gpt_compatible":
            raise ModelConfigError(f"unsupported provider for {tier.name}: {tier.provider}")
        if not tier.ready:
            raise ModelConfigError(
                f"{tier.name} is not ready: set {tier.base_url_env} and {tier.api_key_env}"
            )
        self.tier = tier
        self.timeout = timeout

    def chat_json(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> ModelResponse:
        if self.tier.base_url.startswith("mock://"):
            return mock_response(messages)

        payload: dict[str, Any] = {
            "model": self.tier.model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        if self.tier.reasoning_effort not in {"", "standard"}:
            payload["reasoning_effort"] = self.tier.reasoning_effort

        return self._post_chat(payload)

    def _post_chat(self, payload: dict[str, Any]) -> ModelResponse:
        url = self.tier.base_url.rstrip("/") + "/chat/completions"
        try:
            return self._post_json(url, payload)
        except ModelCallError as exc:
            message = str(exc).lower()
            unsupported = any(token in message for token in ("unsupported", "unknown", "invalid", "unrecognized"))
            if unsupported and ("response_format" in payload or "reasoning_effort" in payload):
                fallback = dict(payload)
                fallback.pop("response_format", None)
                fallback.pop("reasoning_effort", None)
                return self._post_json(url, fallback)
            raise

    def _post_json(self, url: str, payload: dict[str, Any]) -> ModelResponse:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.tier.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - user-configured API relay.
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ModelCallError(f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:  # noqa: BLE001 - keep CLI surface simple.
            raise ModelCallError(str(exc)) from exc

        text = extract_chat_text(raw)
        return ModelResponse(text=text, raw=raw, usage=raw.get("usage", {}))


def extract_chat_text(raw: dict[str, Any]) -> str:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelCallError("response has no choices")
    message = choices[0].get("message", {})
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    raise ModelCallError("response has no text content")


def call_json(
    config: dict[str, Any],
    tier_name: str,
    messages: list[dict[str, str]],
    *,
    trace: TraceLogger,
    stage: str,
    validator,
    temperature: float = 0.2,
    max_attempts: int = 2,
    timeout: float | None = None,
) -> Any:
    tier = get_model_tier(config, tier_name)
    client = GPTCompatibleClient(tier, timeout=timeout or float(config.get("llm_timeout_sec", 90)))
    trace_id = trace.new_id(stage)
    prompt_text = "\n\n".join(message.get("content", "") for message in messages)
    trace.write_artifact(trace_id, "prompt.md", prompt_text)
    started = time.time()
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = client.chat_json(messages, temperature=temperature)
            parsed = parse_json_from_text(response.text)
            normalized = validator(parsed)
            trace.write_artifact(trace_id, f"attempt{attempt}.raw.json", response.raw)
            trace.write_artifact(trace_id, f"attempt{attempt}.parsed.json", normalized)
            trace.write_event(
                {
                    "trace_id": trace_id,
                    "stage": stage,
                    "tier": tier.name,
                    "model": tier.model,
                    "reasoning_effort": tier.reasoning_effort,
                    "prompt_hash": text_hash(prompt_text),
                    "prompt_summary": summarize_text(prompt_text),
                    "attempt": attempt,
                    "ok": True,
                    "elapsed_sec": round(time.time() - started, 3),
                    "usage": response.usage,
                }
            )
            return normalized
        except (ModelCallError, SchemaError, json.JSONDecodeError) as exc:
            last_error = exc
            trace.write_event(
                {
                    "trace_id": trace_id,
                    "stage": stage,
                    "tier": tier.name,
                    "model": tier.model,
                    "attempt": attempt,
                    "ok": False,
                    "error": str(exc),
                }
            )
            if attempt < max_attempts:
                messages = messages + [
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was not valid for the required JSON schema. "
                            "Return only corrected JSON, no Markdown, no commentary."
                        ),
                    }
                ]

    raise ModelCallError(f"{stage} failed after {max_attempts} attempts: {last_error}")


def parse_json_from_text(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start_obj = stripped.find("{")
        end_obj = stripped.rfind("}")
        if start_obj >= 0 and end_obj > start_obj:
            return json.loads(stripped[start_obj : end_obj + 1])
        raise


def mock_response(messages: list[dict[str, str]]) -> ModelResponse:
    prompt = "\n".join(message.get("content", "") for message in messages)
    if "ResearchBrief" in prompt or "brief_extractor" in prompt:
        data = {
            "topic": "mock embodied world action model",
            "problem_statement": "Use action-conditioned world models for contact-rich robot manipulation.",
            "domain": ["embodied intelligence", "world model", "robot manipulation"],
            "known_context": ["world models predict dynamics", "robot tasks require controllability"],
            "constraints": ["moderate compute", "no automatic experiment execution"],
            "non_goals": ["claiming full automation"],
            "success_criteria": ["clear novelty boundary", "minimum experiment plan"],
            "uncertainties": ["closest prior work unknown"],
        }
    elif "ClaimDecomposition" in prompt or "claim_decomposer" in prompt:
        data = {
            "claims": [
                {
                    "id": "C1",
                    "type": "method_hypothesis",
                    "claim": "Action-conditioned world models can expose controllable factors in contact-rich manipulation.",
                    "mechanism": "learn action-to-factor influence rather than only next-state prediction",
                    "task_context": "robot manipulation with contact",
                    "why_it_matters": "It separates controllability from passive prediction.",
                    "risk_if_false": "Prior affordance or controllable representation work may already cover it.",
                    "equivalent_terms": ["affordance learning", "controllable representation"],
                    "search_priority": "high",
                }
            ],
            "risk_questions": ["Has this been done under another name?"],
        }
    elif "QueryPlan" in prompt or "query_planner" in prompt:
        data = {
            "queries": [
                {
                    "id": "Q-C1-exact",
                    "claim_id": "C1",
                    "intent": "exact",
                    "query": "\"action-conditioned world model\" robot manipulation controllability",
                    "rationale": "Direct phrase and domain terms.",
                }
            ]
        }
    elif "NoveltyMatrix" in prompt or "novelty_matrix_builder" in prompt:
        data = {
            "warning": "mock novelty matrix; not a novelty proof",
            "rows": [
                {
                    "claim_id": "C1",
                    "claim": "Action-conditioned world models can expose controllable factors.",
                    "risk": "medium",
                    "closest_papers": [],
                    "missing_evidence": ["Need recent arXiv search"],
                    "positioning": "Frame as controllability boundary, not generic prediction.",
                }
            ],
            "overall_recommendation": "proceed_with_caution",
        }
    elif "ReviewerReport" in prompt or "adversarial_reviewer" in prompt:
        data = {
            "summary": "The idea is plausible but needs a sharper distinction from affordance learning.",
            "score": 7,
            "recommendation": "proceed_with_caution",
            "strongest_objections": ["May be renamed affordance learning."],
            "minimum_fixes": ["Add closest-work comparison and an ablation on controllability representation."],
            "reviewer_likely_prior_work_attack": ["world models", "affordances"],
            "experiment_concerns": ["Need multi-seed evidence."],
            "positioning_advice": "Lead with the controllability diagnostic.",
        }
    elif "IdeaCandidates" in prompt or "idea_refiner" in prompt:
        data = {
            "ideas": [
                {
                    "name": "Controllability-aware WAM",
                    "research_question": "Can WAM learn action-specific controllable factors in contact-rich manipulation?",
                    "method": ["train action-conditioned dynamics", "factorize controllable state dimensions"],
                    "novelty_lever": "controllability boundary",
                    "minimum_experiment": "rope or pushing task with ablation against plain world model",
                    "main_risk": "overlap with affordance work",
                    "expected_contribution": "method",
                    "rank": 1,
                }
            ]
        }
    elif "BottleneckAnalysis" in prompt or "bottleneck_extractor" in prompt:
        data = {
            "bottlenecks": [
                {
                    "id": "B1",
                    "description": "Plain world models may predict contact dynamics without exposing controllable factors.",
                    "why_it_matters": "This creates a publishable diagnostic boundary between prediction and control usefulness.",
                    "current_limit": "Prediction loss can improve while manipulation success does not.",
                    "failure_mode": "The model predicts plausible next states but selects actions that do not change the relevant object state.",
                    "hidden_assumption": "Better predictive dynamics imply better controllability.",
                    "evidence_signal": "A task where prediction error is similar but controllability intervention success diverges.",
                }
            ],
            "hidden_assumptions": ["Prediction quality is a sufficient proxy for action usefulness."],
            "opportunity_map": ["Turn controllability failure into a diagnostic benchmark and training signal."],
        }
    elif "MechanismTransferMap" in prompt or "mechanism_transfer_mapper" in prompt:
        data = {
            "transfers": [
                {
                    "id": "T1",
                    "source_field": "causal representation learning",
                    "source_mechanism": "intervention-based factor identification",
                    "target_bottleneck": "B1",
                    "mapping": {"intervention": "robot action", "causal factor": "controllable object state"},
                    "why_transfer_is_nontrivial": "The mapping must handle continuous contact dynamics rather than static interventions.",
                    "minimum_test": "Compare action intervention success against a plain predictive world model.",
                    "main_risk": "The factorization may collapse to task-specific heuristics.",
                }
            ],
            "do_not_force": ["Do not add diffusion unless it changes the controllability test."],
        }
    elif "IdeaBranches" in prompt or "idea_branch_generator" in prompt:
        data = {
            "branches": [
                {
                    "id": "I1",
                    "name": "Controllability diagnostic for WAMs",
                    "track": "diagnostic",
                    "core_idea": "Build a diagnostic benchmark where predictive accuracy and controllable effect diverge.",
                    "mechanism": "Intervention-style action probes over learned world action models.",
                    "novelty_hypothesis": "The contribution is a diagnostic boundary, not another generic world model.",
                    "minimum_experiment": "Rope or pushing task with matched prediction error and different action effect controllability.",
                    "falsifiable_prediction": "A model can have low prediction error but fail controllability probes.",
                    "closest_prior_work_risk": "Affordance and controllable representation papers may already frame this.",
                    "feasibility_risk": "Probe design may be too task-specific.",
                    "evidence_needed": ["Read affordance learning and controllable representation baselines."],
                },
                {
                    "id": "I2",
                    "name": "Action-factor bottleneck regularizer",
                    "track": "method",
                    "core_idea": "Train a world action model with an auxiliary controllable-factor bottleneck.",
                    "mechanism": "Regularize latent dimensions by action intervention sensitivity.",
                    "novelty_hypothesis": "The method links representation to downstream controllable effects.",
                    "minimum_experiment": "Ablate the bottleneck on contact-rich manipulation tasks.",
                    "falsifiable_prediction": "Removing the bottleneck reduces intervention success more than prediction accuracy.",
                    "closest_prior_work_risk": "May overlap with object-centric or controllable representation learning.",
                    "feasibility_risk": "The regularizer may not be stable.",
                    "evidence_needed": ["Compare against object-centric world model papers."],
                },
            ]
        }
    elif "BranchScreen" in prompt or "branch_screener" in prompt:
        data = {
            "shortlist": [
                {
                    "branch_id": "I1",
                    "decision": "keep",
                    "score": 8,
                    "rationale": "It is narrow, diagnostic, and can be tested cheaply.",
                    "strengths": ["Clear failure mode", "Discriminative experiment"],
                    "fatal_objections": [],
                    "salvage_path": "Frame as benchmark plus analysis if method novelty is weak.",
                    "evidence_needs": ["Closest affordance benchmark comparison"],
                },
                {
                    "branch_id": "I2",
                    "decision": "pivot",
                    "score": 6,
                    "rationale": "Promising but method overlap risk is higher.",
                    "strengths": ["Action-sensitive mechanism"],
                    "fatal_objections": ["Could be a renamed controllable representation loss."],
                    "salvage_path": "Use as secondary method after the diagnostic contribution.",
                    "evidence_needs": ["Object-centric controllability prior work"],
                },
            ],
            "discarded": [],
        }
    elif "StrengthenedIdeas" in prompt or "idea_strengthener" in prompt:
        data = {
            "ideas": [
                {
                    "branch_id": "I1",
                    "name": "Controllability Probe Suite for World Action Models",
                    "research_question": "When does predictive world-model accuracy fail to imply action controllability?",
                    "technical_move": "Create intervention probes over action-conditioned latent factors and compare against prediction-only metrics.",
                    "novelty_lever": "A diagnostic separation between prediction and controllability.",
                    "minimum_experiment": "Two contact-rich tasks where prediction error is matched but controllability probes differ.",
                    "falsifiable_prediction": "Prediction-matched models will rank differently under controllability probes.",
                    "main_risk": "The probe may duplicate affordance evaluation.",
                    "evidence_needed": ["Affordance evaluation papers", "controllable representation papers"],
                    "salvage_from_objections": "Position as a benchmark/diagnostic rather than a broad new architecture.",
                }
            ]
        }
    elif "IdeaSearchResult" in prompt or "decision_chair" in prompt:
        data = {
            "summary": "The diagnostic branch is the strongest because it is narrow, falsifiable, and less dependent on broad method novelty.",
            "final_ideas": [
                {
                    "rank": 1,
                    "branch_id": "I1",
                    "name": "Controllability Probe Suite for World Action Models",
                    "research_question": "When does predictive world-model accuracy fail to imply action controllability?",
                    "technical_move": "Build intervention probes and evaluate prediction-matched WAMs on contact-rich tasks.",
                    "why_now": "World-model robotics papers increasingly need diagnostics beyond prediction loss.",
                    "novelty_lever": "Separates controllability from passive prediction in a measurable benchmark.",
                    "closest_prior_work_attack": "Affordance learning and controllable representation benchmarks.",
                    "minimum_experiment": "Rope or pushing task with prediction-matched models and controllability probe ranking.",
                    "falsifiable_prediction": "Probe ranking will predict downstream action success better than prediction error.",
                    "failure_conditions": ["Affordance benchmarks already measure the same quantity.", "Probe ranking does not predict action success."],
                    "evidence_needed": ["Read closest affordance and controllable representation work."],
                    "decision": "continue",
                }
            ],
            "runner_up_ids": ["I2"],
            "global_risks": ["The contribution may become benchmark-only unless the probe reveals a real failure mode."],
        }
    elif "ResearchOpportunityMap" in prompt or "research_opportunity_miner" in prompt:
        data = {
            "bottleneck_opportunities": [
                {
                    "id": "O1",
                    "bottleneck": "prediction accuracy does not reveal whether a WAM exposes controllable action factors",
                    "why_important": "这是 world model 用于 robot manipulation 时的关键断点，能把预测问题转成可证伪的控制诊断问题。",
                    "evidence_signal": "novelty matrix and reviewer report both warn about affordance / controllable representation overlap.",
                    "mechanism_transfer_candidates": ["intervention-based representation analysis", "causal factor probing"],
                    "novelty_path": "failure_mode",
                    "risk": "需要证明不是已有 affordance benchmark 的改名。",
                    "evidence_needed": ["closest affordance benchmark papers", "controllable representation papers"],
                }
            ],
            "quality_bar_notes": ["优先保留 rough but promising 的诊断型贡献，不追求范式级创新。"],
        }
    elif "ResearchIdeaSet" in prompt or "research_builder" in prompt:
        data = {
            "ideas": [
                {
                    "id": "R1",
                    "name": "WAM controllability failure probe",
                    "seed_source": "O1",
                    "central_insight": "world model 的 prediction loss 可以和 action controllability 脱钩，这个脱钩本身可以成为贡献。",
                    "problem_framing": "从做一个更强 WAM 改成测量 WAM 何时无法支持 controllable action effects。",
                    "nontrivial_mechanism_match": "把 intervention-based representation analysis 映射到 robot action probes，而不是简单换模型。",
                    "technical_move": "设计 action intervention probes，比较 prediction-matched WAMs 的 controllability ranking。",
                    "novelty_boundary": "不是 generic world model architecture，而是 prediction-control gap 的诊断和最小实验协议。",
                    "stronger_baseline_to_beat": "plain predictive world model plus affordance-style evaluation",
                    "minimum_discriminating_experiment": "在 contact-rich pushing/rope 任务中匹配 prediction error，再比较 probe ranking 与 downstream success。",
                    "falsifiable_prediction": "prediction-matched models 会在 controllability probes 上排序不同，且 probe 更能预测 action success。",
                    "failure_value": "如果 probe 无效，也能界定 prediction loss 何时已经足够或 affordance metrics 已覆盖该 gap。",
                    "main_risks": ["与 affordance benchmark 重叠", "probe 可能 task-specific"],
                    "evidence_needed": ["affordance evaluation", "controllable representation learning"],
                    "maturity": "promising",
                }
            ]
        }
    elif "ResearchCriticPanel" in prompt or "research_critic_panel" in prompt:
        data = {
            "panel_summary": "R1 有明确 central insight 和 failure-mode 贡献潜力，但必须把 novelty boundary 从 affordance evaluation 中切出来。",
            "reviews": [
                {
                    "idea_id": "R1",
                    "overall_decision": "repair",
                    "private_scores": {
                        "novelty": 7,
                        "importance": 7,
                        "mechanism": 6,
                        "feasibility": 7,
                        "experiment": 8,
                        "evidence": 5,
                        "publication_potential": 7,
                    },
                    "current_weaknesses": ["closest prior-work boundary still under-specified", "mechanism may look like renamed affordance probing"],
                    "repairable_potential": "把核心收窄为 prediction-control gap 的诊断协议，并要求 stronger baseline 对照。",
                    "irrecoverable_flaws": [],
                    "upgrade_opportunities": ["turn negative cases into a benchmark axis", "add representation intervention analysis"],
                    "better_framing": "When and why does predictive WAM accuracy fail as a proxy for controllable action effects?",
                    "stronger_mechanism_options": ["intervention probes over action-sensitive latent factors"],
                    "missing_evidence": ["recent affordance benchmark papers"],
                    "lens_reviews": [
                        {"lens": "novelty", "finding": "Needs closest-work boundary.", "decision": "repair"},
                        {"lens": "experiment", "finding": "Minimum experiment is discriminating.", "decision": "promising"},
                    ],
                }
            ],
            "hard_reject_ids": [],
        }
    elif "ResearchRevisionSet" in prompt or "research_reviser" in prompt:
        data = {
            "revised_ideas": [
                {
                    "id": "RR1",
                    "source_idea_ids": ["R1"],
                    "name": "Prediction-control gap benchmark for WAMs",
                    "revision_strategy": "repair",
                    "critic_issues_addressed": ["narrowed novelty boundary", "added stronger baseline", "made failure value explicit"],
                    "central_insight": "WAM 的 prediction accuracy 不能自动代表 controllable action effects；这个 gap 可以被系统测量。",
                    "problem_framing": "建立 prediction-control gap 的最小 benchmark，而不是提出泛化的新 architecture。",
                    "nontrivial_mechanism_match": "用 intervention probes 分析 action-sensitive latent factors，并和 affordance-style baseline 区分。",
                    "technical_move": "构造 prediction-matched WAMs，测量 action intervention probes、downstream success 和 affordance baseline 的关系。",
                    "novelty_boundary": "贡献在 diagnostic protocol 和 failure-mode characterization，不在声称全新 world model。",
                    "stronger_baseline_to_beat": "prediction-matched WAM plus affordance-style controllability metric",
                    "minimum_discriminating_experiment": "同 prediction error 下，比较 probe score、affordance metric 与 action success 的相关性。",
                    "falsifiable_prediction": "probe score 会发现 affordance metric 或 prediction loss 看不到的 controllability failure。",
                    "failure_value": "若没有额外信号，则说明已有 affordance metric 已足够，给出负结果边界。",
                    "main_risks": ["benchmark-only contribution may be considered narrow"],
                    "evidence_needed": ["latest affordance and controllability benchmarks"],
                    "maturity": "promising",
                }
            ],
            "discarded": [],
        }
    elif "ResearchChairDecision" in prompt or "research_chair" in prompt:
        data = {
            "summary": "最值得继续的是把 idea 收窄成 prediction-control gap 的 benchmark/diagnostic 贡献；当前不应包装成大 architecture 创新。",
            "final_ideas": [
                {
                    "rank": 1,
                    "idea_id": "RR1",
                    "name": "Prediction-control gap benchmark for WAMs",
                    "decision": "needs_evidence",
                    "why_selected": "它有明确 central insight、可区分实验和可修复的 prior-work 风险。",
                    "central_insight": "prediction accuracy 与 controllable action effects 的脱钩是可测量且有用的 failure mode。",
                    "novelty_boundary": "diagnostic protocol and failure characterization, not a generic WAM architecture.",
                    "stronger_baseline_to_beat": "prediction-matched WAM plus affordance-style controllability metric",
                    "minimum_discriminating_experiment": "比较 prediction error、probe score、affordance metric 与 downstream success 的相关性。",
                    "failure_conditions": ["affordance metrics already capture the same gap", "probe score does not predict action success"],
                    "next_literature_checks": ["affordance evaluation benchmarks", "controllable representation learning"],
                    "next_experiment_checks": ["toy contact-rich task with prediction-matched models", "probe-vs-success correlation"],
                }
            ],
            "promising_pivots": ["If novelty is covered, pivot to a negative-result benchmark on WAM controllability limits."],
            "rejected": [],
            "global_risks": ["The work must avoid becoming just another benchmark without a central failure-mode insight."],
        }
    else:
        data = {
            "objective": "Validate a controllability-aware world action model.",
            "phases": [{"name": "sanity", "goal": "run baseline", "acceptance": "baseline is stable"}],
            "baselines": ["plain world model"],
            "metrics": ["success rate", "prediction error", "ablation delta"],
            "ablations": ["remove controllability head"],
            "failure_criteria": ["no ablation delta"],
            "results_to_claims": [
                {
                    "possible_result": "positive ablation delta",
                    "allowed_claim": "controllability component helps in this setting",
                    "forbidden_claim": "general embodied intelligence solved",
                }
            ],
        }

    return ModelResponse(
        text=json.dumps(data, ensure_ascii=False),
        raw={"choices": [{"message": {"content": json.dumps(data, ensure_ascii=False)}}], "usage": {"mock": True}},
        usage={"mock": True},
    )
