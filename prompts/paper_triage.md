# paper_triage

You are triaging paper metadata for a research novelty check.

Task:
For each paper, judge whether it is relevant to each claim. Be conservative: if a title/abstract might overlap with a claim under different terminology, mark it at least medium.

Return only JSON matching PaperTriage:

```json
{
  "papers": [
    {
      "paper_id": "source identifier or title",
      "title": "paper title",
      "relevance": "high|medium|low|irrelevant",
      "matched_claims": ["C1"],
      "core_contribution": "one sentence",
      "why_relevant": "evidence from title/abstract",
      "risk_signal": "what novelty risk this paper creates"
    }
  ]
}
```
