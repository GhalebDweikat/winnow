# Jev as a context judge for Claude Code: first calibration numbers

*Draft, 16 September 2026. Numbers are from one developer's machine; the 100 hand labels are a model's, pending a human audit.*

## The idea

Coding agents fill their context with tool output the task never needed: license headers, the other 400 lines of the file, 70 lines of a build log that all say `ok`. Summarizing everything is lossy and slow. Byte thresholds are blind to relevance. What you actually want is a yes/no per block, "will the agent need this?", answered fast, cheaply, and with a probability you can set a threshold on.

TypeSafe's Jev is a model built for exactly that shape of question: it returns calibrated probabilities for typed questions instead of generating text, and answers a hundred of them in one call. winnow is a Claude Code plugin that puts Jev between the tools and the context. Every large `Read`, `Bash` or `Grep` result is split into 25-line blocks, Jev answers "is this block needed for the current task?" for each, and blocks below a threshold are replaced by a stub with a recall key. Nothing is lost; it just stops costing tokens until asked for.

The interesting question is not whether this works mechanically (it does; a 107-line file came back as 44 lines in a live session), but whether the probabilities can be trusted. This is the first attempt to measure that on real agent workload.

## Method

**A benchmark from history.** Claude Code keeps session transcripts. Every large tool result in them is followed by what the agent did next, so each block gets a weak label for free: *needed* if a later edit, command or message in the next twelve actions reused one of its lines or named a distinctive identifier from it, *not needed* otherwise. 300 cases and 1,515 blocks came out of my own transcripts in two seconds.

**A baseline.** A keyless lexical judge scores each block by the share of task words it contains. Any real judge has to beat it.

**Hand labels.** Weak labels are biased both ways, so 100 blocks were sampled, stratified by Jev's probability, shuffled, and labeled blind (no probability, no weak label shown) from the task and the block alone.

**Question variants.** Three phrasings of the per-block question were run on identical cases and scored on the same labels.

## Results

Jev against the baseline on all 1,515 weak-labeled blocks:

| | Jev | lexical |
|---|---|---|
| Expected calibration error | **0.14** | 0.31 |
| Median latency per case | 86 ms | n/a |
| Cost, 300 cases | $0.036 | 0 |

Weak label versus hand label on 97 decided blocks: 69% agreement, with disagreements in both directions (20 needed blocks the weak label missed, 10 it over-marked). The weak label is a rough guide, not ground truth.

Jev against the hand labels, default question:

| Jev's probability | blocks | actually needed |
|---|---|---|
| under 0.1 | 10 | **0%** |
| 0.1 to 0.2 | 37 | 32% |
| 0.2 to 0.5 | 25 | 60% |
| 0.5 to 0.7 | 18 | 72% |
| over 0.7 | 7 | 100% |

The ordering is right everywhere and the most confident "no" bin is clean. Above 0.1 Jev is underconfident: things it calls 15% likely are needed a third of the time. That sets the safe operating point at 0.1.

Question phrasing moves the curve. With criteria written as `what` / `not_for` / `examples` objects instead of one-line descriptions, 23 blocks land under 0.1 instead of 10, still with 0% needed, and population-level hidden text at that threshold rises from 1.8% to 4.6%. A stricter phrasing ("directly about the task") hides more but puts needed blocks in the bottom bin 23% of the time, so it is out.

## What it means

- Jev is a real judge for this task and the cheap baseline is not. Ordering and the bottom bin are trustworthy; the middle is underconfident.
- At the safe threshold with the best phrasing, winnow hides about 5% of large-result text with no hand-label regret. That is modest. The path to more is fixing the underconfidence in the 0.1 to 0.5 range, and the harness makes each attempt a few cents.
- Latency is not the problem: 86 ms per judged result in replay, and the plugin's resident sidecar takes hook overhead from 381 ms to 16 ms.
- The weak label is useful for ordering judges and useless for absolute regret. Hand labels are needed for the number people will quote.

## Caveats

One developer's transcripts, heavily Python and Markdown. 97 hand labels, produced by a model rather than a person, with a human audit of 20 pending. Stratified sampling means the aggregate hand-label numbers are not population estimates; only the per-bin rates are. Blocks are 25 lines and boundaries are arbitrary.

## Reproduce

```bash
claude plugin marketplace add GhalebDweikat/winnow && claude plugin install winnow@winnow
winnow replay run --judge lexical
winnow replay judge --judge typesafe --questions structured
winnow replay sample --judged ~/.winnow/replay/judged-typesafe-structured.jsonl
winnow replay label --sample ~/.winnow/replay/sample.jsonl --labeler you
winnow replay score --judged ~/.winnow/replay/judged-typesafe-structured.jsonl --labels ~/.winnow/replay/labels.jsonl
```

Score files for every run above are in `docs/results/2026-09-16/`.
