**English** | [한국어](./README.ko.md)

# explain-diff-plus

A skill that turns a code change (diff·commit·branch·PR) into an interactive HTML page: 

**Explain** tab: 
> A detailed walkthrough of the code change, unfolding as Background → Intuition → Code → Quiz 

**Review** tab: 
> The full diff with inline comments anchored to specific lines.

- Works with any coding agent that can read a `SKILL.md` file and follow it — tested with Codex and Claude.

<table>
<tr>
<td><img src="./docs/explain-light.png" alt="Explain tab, light theme"></td>
<td><img src="./docs/explain-dark.png" alt="Explain tab, dark theme"></td>
</tr>
<tr>
<td><img src="./docs/review-light.png" alt="Review tab, light theme"></td>
<td><img src="./docs/review-dark.png" alt="Review tab, dark theme"></td>
</tr>
</table>

## Inspired by Geoffrey Litt's explain-diff

Inspired by [Geoffrey Litt](https://github.com/geoffreylitt)'s
[explain-diff gist](https://gist.github.com/geoffreylitt/a29df1b5f9865506e8952488eac3d524)
(`explain-diff-html.md`, no license attached). The Background/Intuition/Code/Quiz structure and
the five-question quiz format carry over that original idea; the wording throughout — SKILL.md,
the assembler, the template — was written independently for this repo. See
[`NOTICE.md`](./NOTICE.md) for exactly where the line is drawn.

### What this project adds beyond that idea:

- **Review tab** 
  - Per-file diff accordion + comment cards anchored to specific lines
  - `scripts/render_diff.py` — reads the real `git diff` and assembles the page, instead of the
    agent hand-transcribing a diff into HTML
  - Light/dark theme, line-wrap toggles, per-tab scroll memory
  - A copy button that copies `file:line` on drag-select in a code block 
  - A `--lang` switch (`en`/`ko`) for the page's static chrome
  - A writing-style guide (consistent tone, plain non-literary titles, comment density/overlap rules)

## Usage

1. Put this repo wherever your agent looks for skills, or just point it at `SKILL.md` and tell it
   to follow it.
2. Ask it to explain a diff, commit, or PR — however your agent normally triggers a skill.
3. It runs `scripts/render_diff.py --repo <repo> --commit <sha> --list`, writes the Explain-tab
   body and Review-tab comments, then assembles the page with
   `render_diff.py --lang <en|ko>` and opens it.
4. Output goes to `$EXPLAIN_DIFF_GALLERY` if set, else `~/explain-diff-gallery/<project>/<branch>/`,
   else the OS temp dir.

Full rules — workflow, per-section writing guidance, diagram/quiz markup, comment density, and how
chrome language relates to content language — are in [`SKILL.md`](./SKILL.md).

## Requirements

- Python 3 (standard library only)
- `git`

## License

MIT — see [`LICENSE`](./LICENSE). It covers what was newly written for this repo (the assembler
script, the template, the instructions). It does not claim to relicense the structural idea this
project is inspired by; see [`NOTICE.md`](./NOTICE.md).
