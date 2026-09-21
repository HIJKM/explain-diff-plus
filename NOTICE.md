# Notice

This project was inspired by [Geoffrey Litt](https://github.com/geoffreylitt)'s
[explain-diff gist](https://gist.github.com/geoffreylitt/a29df1b5f9865506e8952488eac3d524)
(`explain-diff-html.md`). That gist has no license attached to it.

## What carries over from the original idea

- The overall shape of the explanation: a Background / Intuition / Code / Quiz walkthrough of a
  code change, rather than a plain diff dump.
- The format of the quiz: five medium-difficulty, multiple-choice questions with per-answer
  feedback.
- A handful of general authoring principles (explore the surrounding code before writing
  Background; use toy-data examples and diagrams; avoid ASCII art; assemble to a single output
  page) that the original gist also states.

These are ideas and conventions, not literal text lifted from the gist. Where earlier drafts of
this repo's `SKILL.md` echoed the gist's specific wording too closely (most notably a line
instructing the writer to imitate Martin Kleppmann's prose style, which was close to the
original's own phrasing), that wording has been rewritten independently.

## What is original to this repository

- `scripts/render_diff.py` — the assembler that reads a real `git diff` and builds the page. The
  original gist has no equivalent; it leaves diff-to-HTML transcription entirely to the agent.
- `assets/explainer-template.html` — the HTML/CSS/JS template: the two-tab layout, the Review tab's
  per-file accordion and line-anchored comment cards, the light/dark theme, the line-wrap toggles,
  the `--lang` chrome-localization mechanism, drag-select `file:line` copying, and all of the
  visual design.
- The instructional text in `SKILL.md` beyond the ideas listed above: the writing-register
  guidance, the title/lede plain-language rules, the comment density and non-overlap rules, the
  quiz markup contract, and the chrome-language-vs-content-language mechanism.
- `CONTEXT.md`, the bilingual `README.md`/`README.ko.md`, and the screenshots under `docs/`.

## License scope

The MIT license in [`LICENSE`](./LICENSE) applies to the material described above as original to
this repository. It does not purport to relicense Geoffrey Litt's gist, which remains his and
carries no license of its own. Crediting the original as inspiration (as this repo's README and
`SKILL.md` do) is a courtesy and does not itself grant or receive any additional rights.
