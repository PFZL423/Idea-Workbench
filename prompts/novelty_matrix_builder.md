# novelty_matrix_builder

You are building a claim-by-paper novelty matrix for a robotics / ML idea.

Rules:
- Do not claim that the idea is novel.
- First identify overlap evidence, then differences.
- If `evidence_qa` is present, prioritize its claim-level answers over title/abstract metadata.
- If evidence QA is unavailable or says no local PDFs were present, mark missing evidence explicitly.
- Consider renamed equivalents: affordance learning, controllable representation, action-conditioned dynamics, model-based RL, world models, dynamics models, contact-rich manipulation, deformable object manipulation.
- If evidence is missing, say so explicitly.
- Applying X to Y is not enough unless the application reveals a new finding or failure mode.

Return only JSON matching NoveltyMatrix:

```json
{
  "warning": "short warning that this is not novelty proof",
  "rows": [
    {
      "claim_id": "C1",
      "claim": "claim text",
      "risk": "high|medium|low|unknown",
      "closest_papers": [
        {
          "title": "paper title",
          "year": "year",
          "url": "url",
          "overlap": "what is similar",
          "difference": "what remains different",
          "evidence_strength": "strong|medium|weak"
        }
      ],
      "missing_evidence": ["what still needs reading/search"],
      "positioning": "how to position the claim if kept"
    }
  ],
  "overall_recommendation": "proceed|proceed_with_caution|pivot|abandon"
}
```
