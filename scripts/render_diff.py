#!/usr/bin/env python3
"""
render_diff.py -- explain-diff page assembler. Uses only the Python 3 standard library.

    python render_diff.py --repo . --commit a3f9c21 --list
    python render_diff.py --repo . --commit a3f9c21 \
        --explain explain-main.html --comments comments.json --meta meta.json --open

What it does
  1. Reads a git diff and builds a per-file accordion (the review tab). Old/new line
     numbers, hunk headers, syntax highlighting (.kw .fn .st .cm .va); generated files
     are collapsed behind "N lines collapsed - Expand".
  2. Replaces <!-- snippet: path:8-16 --> placeholders in the explain-tab body with
     actual diff excerpts (.gh-diff).
  3. Reads comments.json, tags diff rows with data-rv, and builds the right-column
     cards (.rv-card).
  4. Fills in the @@markers of assets/explainer-template.html. The default output path
     is <gallery>/<project>/<branch>/YYYY-MM-DD-explanation-<slug>.html, where
     <gallery> is $EXPLAIN_DIFF_GALLERY if set, else ~/explain-diff-gallery, falling
     back to the system temp dir if that directory can't be created.

comments.json
  [{"file": "src/x.ts", "side": "new", "start": 25, "end": 27,
    "body": "<p>...</p>", "suggest": "code"}]
  side defaults to "new". Omitting start/end anchors the comment as a file-level
  summary (anchored to the header).

meta.json
  {"title": "one-line essence<br>second line", "lede": "2-3 sentences", "author": "name", "slug": "fclt-search"}
  author falls back to git if omitted; slug falls back to the sha if omitted.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
HERE = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = HERE.parent / "assets" / "explainer-template.html"
# Gallery folder where generated explain-diff HTML pages are collected on this machine.
# Override with the EXPLAIN_DIFF_GALLERY env var; defaults to ~/explain-diff-gallery.
# Falls back to the system temp dir if that directory can't be created.
# Actual files land at <gallery>/<project>/<branch>/YYYY-MM-DD-explanation-<slug>.html
DEFAULT_OUT_DIR = Path(os.environ.get("EXPLAIN_DIFF_GALLERY", str(Path.home() / "explain-diff-gallery")))


def gallery_project_name(repo: Path) -> str:
    """Repository folder name. For a linked worktree this is usually the repo's own name (e.g. "myrepo")."""
    try:
        top = run_git(repo, "rev-parse", "--show-toplevel", check=False).strip()
        if top:
            return Path(top).name
    except Exception:
        pass
    return repo.name


def gallery_worktree_name(repo: Path) -> str:
    """Uses the current branch name as the worktree folder. Falls back to HEAD's short hash when detached."""
    branch = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
    if branch and branch != "HEAD":
        # Replace only characters that are invalid in Windows paths
        return re.sub(r'[<>:"/\\|?*]', "-", branch)
    short = run_git(repo, "rev-parse", "--short=7", "HEAD", check=False).strip()
    return short or "worktree"


def default_gallery_out(repo: Path, slug: str) -> Path:
    try:
        DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return Path(tempfile.gettempdir()) / f"{_dt.date.today().isoformat()}-explanation-{slug}.html"
    out_dir = DEFAULT_OUT_DIR / gallery_project_name(repo) / gallery_worktree_name(repo)
    return out_dir / f"{_dt.date.today().isoformat()}-explanation-{slug}.html"


# --------------------------------------------------------------------------- i18n
# UI strings this script bakes directly into the generated page (badges, buttons,
# links). The template (assets/explainer-template.html) carries a matching JS
# I18N table for the strings *it* generates client-side (tab labels, toggles,
# aria-labels, the quiz, etc.) — the two tables are kept in sync by hand, key by
# key, for the languages listed here. Add a language by adding an entry to both.
STRINGS = {
    "en": {
        "comments_badge": lambda n: f"{n} comment" if n == 1 else f"{n} comments",
        "comments_stat_html": lambda n: f"<b>{n}</b> comments",
        "elide": lambda n: f"{n} lines collapsed - Expand",
        "suggestion": "Suggestion",
        "view_full_diff": "View in full diff →",
        "whole_file": "whole file",
        "snippet_wrap_off": "Turn off line wrap",
        "default_title": lambda label: f"Explaining changes in {label}",
        "and_more": lambda n: f" and {n} more",
    },
    "ko": {
        "comments_badge": lambda n: f"코멘트 {n}개",
        "comments_stat_html": lambda n: f"코멘트 <b>{n}</b>",
        "elide": lambda n: f"{n}줄 생략 · 펼치기",
        "suggestion": "제안",
        "view_full_diff": "전체 diff에서 보기 →",
        "whole_file": "파일 전체",
        "snippet_wrap_off": "줄바꿈 끄기",
        "default_title": lambda label: f"{label} 변경 설명",
        "and_more": lambda n: f" 외 {n}개",
    },
}


def strings_for(lang: str) -> dict:
    if lang not in STRINGS:
        eprint(f"[warn] unsupported --lang '{lang}', falling back to 'en' (supported: {', '.join(STRINGS)})")
        return STRINGS["en"]
    return STRINGS[lang]


GENERATED_PATTERNS = [
    r"(^|/)package-lock\.json$", r"(^|/)yarn\.lock$", r"(^|/)pnpm-lock\.yaml$",
    r"(^|/)Cargo\.lock$", r"(^|/)poetry\.lock$", r"(^|/)Gemfile\.lock$", r"(^|/)composer\.lock$",
    r"(^|/)go\.sum$", r"(^|/)uv\.lock$", r"\.min\.(js|css)$", r"\.map$", r"(^|/)__snapshots__/",
    r"\.snap$", r"(^|/)dist/", r"(^|/)build/", r"\.generated\.\w+$", r"\.g\.dart$", r"\.pb\.go$",
]
GENERATED_MAX_CHANGED = 400


def eprint(*a):
    print(*a, file=sys.stderr)


# --------------------------------------------------------------------------- git
def run_git(repo: Path, *args: str, check: bool = True) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if check and p.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{p.stderr.decode('utf-8', 'replace')}")
    return p.stdout.decode("utf-8", "replace")


def git_ok(repo: Path, *args: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True).returncode == 0


# --------------------------------------------------------------------------- diff model
@dataclass
class Row:
    kind: str            # ctx | add | del
    old: Optional[int]
    new: Optional[int]
    pos_new: int         # position of this row in terms of the new file (for "del" rows, the next new line number)
    text: str
    rv: list = field(default_factory=list)


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    ctx: str
    rows: list = field(default_factory=list)


@dataclass
class DFile:
    old_path: Optional[str]
    new_path: Optional[str]
    status: str = "modified"     # added | deleted | modified | renamed
    binary: bool = False
    hunks: list = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or "?"

    @property
    def slug(self) -> str:
        return "f-" + re.sub(r"[^A-Za-z0-9]+", "-", self.path).strip("-").lower()

    @property
    def adds(self) -> int:
        return sum(1 for h in self.hunks for r in h.rows if r.kind == "add")

    @property
    def dels(self) -> int:
        return sum(1 for h in self.hunks for r in h.rows if r.kind == "del")

    @property
    def rows(self):
        for h in self.hunks:
            for r in h.rows:
                yield r


HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")


def _unquote(p: str) -> str:
    p = p.strip()
    if p.startswith('"') and p.endswith('"'):
        p = bytes(p[1:-1], "utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8", "replace")
    return p


def parse_unified(text: str) -> list[DFile]:
    files: list[DFile] = []
    cur: Optional[DFile] = None
    hunk: Optional[Hunk] = None
    old_no = new_no = 0
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("diff --git "):
            cur = DFile(None, None)
            files.append(cur)
            hunk = None
            m = re.match(r'^diff --git (?:"a/(.+?)"|a/(\S+)) (?:"b/(.+?)"|b/(\S+))$', ln)
            if m:
                cur.old_path = _unquote(m.group(1) or m.group(2))
                cur.new_path = _unquote(m.group(3) or m.group(4))
            i += 1
            continue
        if cur is None:
            i += 1
            continue
        if hunk is None or not (ln[:1] in (" ", "+", "-", "\\") and not ln.startswith(("--- ", "+++ "))):
            if ln.startswith("new file mode"):
                cur.status = "added"
            elif ln.startswith("deleted file mode"):
                cur.status = "deleted"
            elif ln.startswith("rename from "):
                cur.status = "renamed"
                cur.old_path = _unquote(ln[len("rename from "):])
            elif ln.startswith("rename to "):
                cur.new_path = _unquote(ln[len("rename to "):])
            elif ln.startswith("Binary files") or ln.startswith("GIT binary patch"):
                cur.binary = True
            elif ln.startswith("--- "):
                p = ln[4:]
                cur.old_path = None if p.strip() == "/dev/null" else _unquote(re.sub(r"^a/", "", p))
            elif ln.startswith("+++ "):
                p = ln[4:]
                cur.new_path = None if p.strip() == "/dev/null" else _unquote(re.sub(r"^b/", "", p))
            elif ln.startswith("@@"):
                m = HUNK_RE.match(ln)
                if m:
                    hunk = Hunk(int(m.group(1)), int(m.group(2) or 1), int(m.group(3)), int(m.group(4) or 1), m.group(5))
                    cur.hunks.append(hunk)
                    old_no, new_no = hunk.old_start, hunk.new_start
            i += 1
            continue
        # hunk body
        c = ln[:1]
        body = ln[1:]
        if c == " ":
            hunk.rows.append(Row("ctx", old_no, new_no, new_no, body))
            old_no += 1
            new_no += 1
        elif c == "+":
            hunk.rows.append(Row("add", None, new_no, new_no, body))
            new_no += 1
        elif c == "-":
            hunk.rows.append(Row("del", old_no, None, new_no, body))
            old_no += 1
        # '\ No newline at end of file' -> ignored
        i += 1
    for f in files:
        if f.status == "modified" and f.old_path is None:
            f.status = "added"
        if f.status == "modified" and f.new_path is None:
            f.status = "deleted"
    return files


# --------------------------------------------------------------------------- highlight
CLIKE_KW = set("""
abstract as async await break case catch class const continue debugger default delete do else enum export
extends finally for from function get if implements import in instanceof interface let new of package private
protected public readonly return set static super switch throw try type typeof var void while with yield declare
namespace keyof infer satisfies override
boolean byte char double float int long short throws final synchronized volatile transient native assert
fun val when object data sealed open companion init lateinit suspend inline typealias is out
func struct map chan go defer select range
fn mut impl trait match use mod pub crate where unsafe move ref dyn
using string bool foreach
guard protocol extension late
""".split())
CLIKE_VA = set("true false null undefined this self None True False NaN Infinity nil".split())
PY_KW = set("""
and as assert async await break class continue def del elif else except finally for from global if import in is
lambda nonlocal not or pass raise return try while with yield match case
""".split())
PY_VA = set("True False None self cls".split())
SQL_KW = set("""
select from where insert into values update set delete create table alter drop index view join inner left right
outer full cross on as and or not null is in exists between like ilike group by order having limit offset union
all distinct primary key foreign references default constraint unique check case when then else end begin commit
rollback with recursive returning if exists cascade add column rename to truncate grant revoke declare cursor
procedure function trigger before after each row execute
""".split())
SH_KW = set("""
if then else elif fi for while do done case esac in function return local export source exit
param begin process end foreach switch try catch finally throw filter class
""".split())

LANG_BY_EXT = {
    "ts": "clike", "tsx": "clike", "js": "clike", "jsx": "clike", "mjs": "clike", "cjs": "clike",
    "java": "clike", "kt": "clike", "kts": "clike", "go": "clike", "c": "clike", "h": "clike", "cpp": "clike",
    "cc": "clike", "hpp": "clike", "cs": "clike", "swift": "clike", "rs": "clike", "dart": "clike",
    "scala": "clike", "php": "clike", "groovy": "clike", "gradle": "clike", "m": "clike",
    "py": "python", "pyi": "python",
    "sql": "sql", "ddl": "sql", "dml": "sql",
    "html": "markup", "htm": "markup", "xml": "markup", "vue": "markup", "svelte": "markup", "xhtml": "markup",
    "jsp": "markup", "svg": "markup", "hwpx": "markup",
    "css": "css", "scss": "css", "less": "css",
    "json": "json", "jsonc": "json",
    "yml": "yaml", "yaml": "yaml", "toml": "yaml", "ini": "yaml", "properties": "yaml", "env": "yaml", "cfg": "yaml",
    "sh": "shell", "bash": "shell", "zsh": "shell", "ps1": "shell", "psm1": "shell", "bat": "shell", "cmd": "shell",
}


def lang_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if name.lower() in ("dockerfile", "makefile"):
        return "shell"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return LANG_BY_EXT.get(ext, "plain")


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def span(cls: str, s: str) -> str:
    return f'<span class="{cls}">{esc(s)}</span>' if s else ""


class Highlighter:
    """Line-by-line tokenizer. Carries block-comment / multi-line-string state across lines within a file."""

    def __init__(self, lang: str):
        self.lang = lang
        self.block: Optional[str] = None   # 'cm' | 'tpl' | '"""' | "'''"

    # ---- per-language master patterns
    _clike = re.compile(
        r"(?P<cm>/\*.*?\*/|/\*.*$|//.*$)"
        r"|(?P<tpl>`(?:\\.|[^`\\])*`|`(?:\\.|[^`\\])*$)"
        r"|(?P<st>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
        r"|(?P<num>\b\d[\w.]*\b)"
        r"|(?P<id>[A-Za-z_$][\w$]*)"
    )
    _py = re.compile(
        r"(?P<cm>#.*$)"
        r"|(?P<tri>[rbufRBUF]{0,2}(?:\"\"\"|'''))"
        r"|(?P<st>[rbufRBUF]{0,2}(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'))"
        r"|(?P<dec>@[\w.]+)"
        r"|(?P<num>\b\d[\w.]*\b)"
        r"|(?P<id>[A-Za-z_][\w]*)"
    )
    _sql = re.compile(
        r"(?P<cm>/\*.*?\*/|/\*.*$|--.*$)"
        r"|(?P<st>'(?:''|[^'])*')"
        r"|(?P<num>\b\d[\w.]*\b)"
        r"|(?P<id>[A-Za-z_][\w]*)"
    )
    _markup = re.compile(
        r"(?P<cm><!--.*?-->|<!--.*$)"
        r"|(?P<tag></?[A-Za-z][\w:.-]*|/?>)"
        r"|(?P<attr>[A-Za-z_:][\w:.-]*(?==))"
        r"|(?P<st>\"[^\"]*\"|'[^']*')"
    )
    _css = re.compile(
        r"(?P<cm>/\*.*?\*/|/\*.*$)"
        r"|(?P<st>\"[^\"]*\"|'[^']*')"
        r"|(?P<at>@[\w-]+)"
        r"|(?P<prop>[\w-]+(?=\s*:))"
        r"|(?P<sel>[.#][\w-]+)"
        r"|(?P<num>\b\d[\w.%]*\b)"
    )
    _json = re.compile(
        r"(?P<key>\"(?:\\.|[^\"\\])*\"(?=\s*:))"
        r"|(?P<st>\"(?:\\.|[^\"\\])*\")"
        r"|(?P<kw>\b(?:true|false|null)\b|-?\b\d[\w.+-]*\b)"
    )
    _yaml = re.compile(
        r"(?P<cm>#.*$)"
        r"|(?P<key>^\s*-?\s*[\w.\-\[\]\"']+(?=\s*[:=]))"
        r"|(?P<st>\"(?:\\.|[^\"\\])*\"|'[^']*')"
        r"|(?P<kw>\b(?:true|false|null|yes|no|on|off)\b)"
    )
    _shell = re.compile(
        r"(?P<cm>#.*$)"
        r"|(?P<st>\"(?:\\.|[^\"\\])*\"|'[^']*')"
        r"|(?P<va>\$\{?[\w:-]+\}?)"
        r"|(?P<id>[A-Za-z_][\w-]*)"
    )

    def line(self, text: str) -> str:
        if self.lang == "plain":
            return esc(text)
        out = []
        pos = 0
        # Continue any in-progress block state
        if self.block:
            end = self._block_end(text)
            if end is None:
                return span(self._block_cls(), text)
            out.append(span(self._block_cls(), text[:end]))
            pos = end
            self.block = None
        pat = getattr(self, "_" + {"clike": "clike", "python": "py", "sql": "sql", "markup": "markup",
                                   "css": "css", "json": "json", "yaml": "yaml", "shell": "shell"}[self.lang])
        for m in pat.finditer(text, pos):
            if m.start() > pos:
                out.append(esc(text[pos:m.start()]))
            out.append(self._token(m, text))
            pos = m.end()
        out.append(esc(text[pos:]))
        return "".join(out)

    def _block_cls(self) -> str:
        return "cm" if self.block == "cm" else "st"

    def _block_end(self, text: str) -> Optional[int]:
        term = {"cm": "*/" if self.lang != "markup" else "-->", "tpl": "`", '"""': '"""', "'''": "'''"}[self.block]
        i = text.find(term)
        return None if i < 0 else i + len(term)

    def _token(self, m: re.Match, text: str) -> str:
        g = m.lastgroup
        s = m.group(0)
        if g == "cm":
            if (s.startswith("/*") and not s.endswith("*/")) or (s.startswith("<!--") and not s.endswith("-->")):
                self.block = "cm"
            return span("cm", s)
        if g == "tpl":
            if not (len(s) > 1 and s.endswith("`") and not s.endswith("\\`")):
                self.block = "tpl"
            return span("st", s)
        if g == "tri":
            q = s[-3:]
            rest = text[m.end():]
            j = rest.find(q)
            if j < 0:
                self.block = q
                # Treat the rest of the line as a string: since finditer keeps advancing, we
                # can't return the remainder from here. Instead, only color the opening quote
                # and carry the rest forward as block state so the next token doesn't process it.
                return span("st", s)
            return span("st", s)
        if g in ("st", "key", "attr", "prop", "at", "sel", "tag", "dec", "va", "kw", "num"):
            cls = {"st": "st", "key": "va", "attr": "va", "prop": "va", "at": "kw", "sel": "fn",
                   "tag": "kw", "dec": "fn", "va": "va", "kw": "kw", "num": "va"}[g]
            return span(cls, s)
        if g == "id":
            low = s.lower()
            nxt = text[m.end():m.end() + 1]
            if self.lang == "clike":
                if s in CLIKE_KW:
                    return span("kw", s)
                if s in CLIKE_VA or (len(s) > 1 and s.isupper() and "_" in s or (len(s) > 2 and s.isupper())):
                    return span("va", s)
                if nxt == "(":
                    return span("fn", s)
            elif self.lang == "python":
                if s in PY_KW:
                    return span("kw", s)
                if s in PY_VA or (len(s) > 2 and s.isupper()):
                    return span("va", s)
                if nxt == "(":
                    return span("fn", s)
            elif self.lang == "sql":
                if low in SQL_KW:
                    return span("kw", s)
                if nxt == "(":
                    return span("fn", s)
            elif self.lang == "shell":
                if low in SH_KW or s in SH_KW:
                    return span("kw", s)
            return esc(s)
        return esc(s)


def highlight_rows(rows, lang: str) -> list[str]:
    """Highlights a list of rows in order. add/ctx rows continue the new-file state, while del rows continue the old-file state separately."""
    hn, ho = Highlighter(lang), Highlighter(lang)
    out = []
    for r in rows:
        out.append((ho if r.kind == "del" else hn).line(r.text))
    return out


# --------------------------------------------------------------------------- generated / lists
def is_generated(path: str, changed: int) -> bool:
    if changed > GENERATED_MAX_CHANGED:
        return True
    return any(re.search(p, path) for p in GENERATED_PATTERNS)


# --------------------------------------------------------------------------- comments
@dataclass
class Comment:
    file: str
    side: str
    start: Optional[int]
    end: Optional[int]
    body: str
    suggest: Optional[str]
    id: str = ""
    dfile: Optional[DFile] = None
    rows: list = field(default_factory=list)
    order: tuple = (0, 0)
    span: Optional[tuple] = None   # (first row index, last row index) -- relative to dfile.rows


def load_comments(path: Optional[Path], files: list[DFile]) -> list[Comment]:
    if not path:
        return []
    data = json.loads(Path(path).read_text("utf-8"))
    by_path = {}
    for i, f in enumerate(files):
        if f.new_path:
            by_path[f.new_path] = (i, f)
        if f.old_path:
            by_path.setdefault(f.old_path, (i, f))
    out: list[Comment] = []
    for raw in data:
        c = Comment(raw["file"], raw.get("side", "new"), raw.get("start"), raw.get("end", raw.get("start")),
                    raw.get("body", ""), raw.get("suggest"))
        hit = by_path.get(c.file)
        if not hit:
            eprint(f"[warn] comment file not found in diff, skipping: {c.file}")
            continue
        fi, f = hit
        c.dfile = f
        if c.start is None:
            c.order = (fi, -1, 0)
        else:
            idx = last = -1
            for k, r in enumerate(f.rows):
                no = r.new if c.side == "new" else r.old
                if no is not None and c.start <= no <= (c.end or c.start):
                    c.rows.append(r)
                    if idx < 0:
                        idx = k
                    last = k
            if not c.rows:
                eprint(f"[warn] no diff row for {c.file}:{c.start}-{c.end} ({c.side}); anchoring to the file header instead")
                c.start = c.end = None
                c.order = (fi, -1, 0)
            else:
                c.order = (fi, idx, 0)
                c.span = (idx, last)
        out.append(c)
    out.sort(key=lambda c: c.order)
    for n, c in enumerate(out, 1):
        c.id = f"c{n}"
        for r in c.rows:
            r.rv.append(c.id)
    # Comments whose ranges overlap in terms of rendered diff rows (e.g. a "new"-side
    # L3-12 comment that has an "old"-side L9-13 deletion block nested inside it).
    # The page handles resuming the outer range once the inner one ends, but a card
    # ends up highlighted twice, so splitting the ranges is preferable when possible.
    prev: Optional[Comment] = None
    for c in out:
        if prev is not None and prev.dfile is c.dfile and prev.span and c.span and c.span[0] <= prev.span[1]:
            eprint(f"[warn] overlapping ranges: {prev.id} {loc_of(prev)} contains {c.id} {loc_of(c)} -- "
                   f"merge the deletion block into one comment with the surrounding addition block, or split the ranges")
        if prev is None or prev.dfile is not c.dfile or not prev.span or (c.span and c.span[1] > prev.span[1]):
            prev = c
    return out


def loc_of(c: "Comment") -> str:
    if c.start is None:
        return "(file)"
    side = "old " if c.side == "old" else ""
    return f"{side}L{c.start}-{c.end}" if c.end and c.end != c.start else f"{side}L{c.start}"


# --------------------------------------------------------------------------- render: review
def render_stat(adds: int, dels: int, cls: str = "gh-stat") -> str:
    parts = []
    if adds:
        parts.append(f'<span class="add">+{adds}</span>')
    if dels:
        parts.append(f'<span class="del">-{dels}</span>')
    return f'<span class="{cls}">{"".join(parts)}</span>'


def render_review_file(f: DFile, comments: list[Comment], ui_lang: str = "en") -> str:
    S = strings_for(ui_lang)
    lang = lang_of(f.path)
    changed = f.adds + f.dels
    gen = is_generated(f.path, changed) and not f.binary
    n_comments = sum(1 for c in comments if c.dfile is f)
    file_level = [c for c in comments if c.dfile is f and c.start is None]
    summary_rv = f' data-rv="{" ".join(c.id for c in file_level)}"' if file_level else ""
    badges = [f'<span class="rv-badge {f.status}">{f.status}</span>']
    if gen:
        badges.append('<span class="rv-badge generated">generated</span>')
    if f.binary:
        badges.append('<span class="rv-badge">binary</span>')
    path_html = esc(f.path)
    if f.status == "renamed" and f.old_path and f.old_path != f.new_path:
        path_html = f'{esc(f.old_path)} → {esc(f.new_path)}'
    cnt = f'<span class="rv-cnt">{S["comments_badge"](n_comments)}</span>' if n_comments else ""
    head = (
        f'<summary class="rv-file-hd"{summary_rv}><span class="rv-chev"></span>'
        f'<span class="rv-path">{path_html}</span>{"".join(badges)}'
        f'{render_stat(f.adds, f.dels, "rv-stat")}{cnt}</summary>'
    )
    rows_html = []
    if f.binary:
        rows_html.append('<tbody><tr class="bin"><td colspan="4">Binary file -- no content diff</td></tr></tbody>')
    else:
        body = []
        all_rows = list(f.rows)
        colored = highlight_rows(all_rows, lang)
        k = 0
        for h in f.hunks:
            hdr = f"@@ -{h.old_start},{h.old_len} +{h.new_start},{h.new_len} @@"
            ctx = f' <span class="hk">{esc(h.ctx)}</span>' if h.ctx else ""
            body.append(f'<tr class="hunk"><td colspan="4">{hdr}{ctx}</td></tr>')
            for r in h.rows:
                body.append(render_row(r, colored[k], two_cols=True))
                k += 1
        if gen:
            total = len(all_rows)
            inner = (
                f'<div class="rv-elide"><button type="button">{S["elide"](total)}</button></div>'
                f'<table class="gh-diff-tbl rv-tbl" hidden>{COLS2}<tbody>{"".join(body)}</tbody></table>'
            )
        else:
            inner = f'<table class="gh-diff-tbl rv-tbl">{COLS2}<tbody>{"".join(body)}</tbody></table>'
        return (
            f'<details class="rv-file" id="{f.slug}" open data-path="{esc(f.path)}" data-status="{f.status}">'
            f'{head}<div class="rv-file-body">{inner}</div></details>'
        )
    inner = f'<table class="gh-diff-tbl rv-tbl">{COLS2}{"".join(rows_html)}</table>'
    return (
        f'<details class="rv-file" id="{f.slug}" open data-path="{esc(f.path)}" data-status="{f.status}">'
        f'{head}<div class="rv-file-body">{inner}</div></details>'
    )


COLS1 = '<colgroup><col class="c-ln"><col class="c-gut"><col></colgroup>'
COLS2 = '<colgroup><col class="c-ln"><col class="c-ln"><col class="c-gut"><col></colgroup>'


def render_row(r: Row, code_html: str, two_cols: bool) -> str:
    # data-rv only applies in the review tab (two columns). If it were added on explain-tab
    # snippets, hidden rows would end up being picked up as anchors.
    rv = f' data-rv="{" ".join(r.rv)}"' if (r.rv and two_cols) else ""
    sign = {"add": "+", "del": "-", "ctx": ""}[r.kind]
    if two_cols:
        old = "" if r.old is None else str(r.old)
        new = "" if r.new is None else str(r.new)
        return (f'<tr class="{r.kind}"{rv}><td class="ln old">{old}</td><td class="ln new">{new}</td>'
                f'<td class="gutter">{sign}</td><td class="code">{code_html}</td></tr>')
    no = r.new if r.kind != "del" else r.old
    return (f'<tr class="{r.kind}"{rv}><td class="ln">{"" if no is None else no}</td>'
            f'<td class="gutter">{sign}</td><td class="code">{code_html}</td></tr>')


def render_file_links(files: list[DFile]) -> str:
    out = []
    for f in files:
        d, _, base = f.path.rpartition("/")
        dirspan = f'<span class="fdir">{esc(d + "/")}</span>' if d else ""
        out.append(
            f'<a class="file" href="#{f.slug}"><span class="fname">{dirspan}<span class="fbase">{esc(base)}</span></span>'
            f'{render_stat(f.adds, f.dels, "stat")}</a>'
        )
    return "\n".join(out)


def render_comment_cards(comments: list[Comment], ui_lang: str = "en") -> str:
    S = strings_for(ui_lang)
    out = []
    total = len(comments)
    for i, c in enumerate(comments, 1):
        if c.start is None:
            rng = S["whole_file"]
        else:
            side = "" if c.side == "new" else "(old) "
            rng = side + (f"L{c.start}" if c.start == c.end else f"L{c.start}–{c.end}")
        loc = f"{c.dfile.path} · {rng}"
        loc_html = f'<span class="rv-lf">{esc(c.dfile.path)}</span><span class="rv-ll">{esc(rng)}</span>'
        body = c.body.strip()
        if not body.lstrip().startswith("<"):
            body = f"<p>{body}</p>"
        sugg = ""
        if c.suggest:
            hl = Highlighter(lang_of(c.dfile.path))
            rows = "".join(
                f'<tr class="add"><td class="gutter">+</td><td class="code">{hl.line(l)}</td></tr>'
                for l in c.suggest.splitlines()
            )
            sugg = (f'<div class="gh-diff"><div class="gh-diff-hd"><span class="gh-file rv-sg">{S["suggestion"]}</span></div>'
                    f'<table class="gh-diff-tbl"><colgroup><col class="c-gut"><col></colgroup><tbody>{rows}</tbody></table></div>')
        out.append(
            f'<div class="rv-wrap" data-for="{c.id}" hidden><article class="rv-card">'
            f'<div class="rv-hd"><span class="rv-n">{i:02d}</span><span class="rv-loc" title="{esc(loc)}">{loc_html}</span></div>'
            f'{body}{sugg}</article></div>'
        )
    return "\n".join(out)


# --------------------------------------------------------------------------- render: snippets
SNIPPET_RE = re.compile(r"<!--\s*snippet:\s*(.+?):(\d+)(?:\s*[-–]\s*(\d+))?\s*-->")


def file_lines_new(repo: Path, rev: Optional[str], path: str) -> Optional[list[str]]:
    try:
        if rev is None:
            return (repo / path).read_text("utf-8", "replace").splitlines()
        return run_git(repo, "show", f"{rev}:{path}").splitlines()
    except SystemExit:
        return None
    except OSError:
        return None


def render_snippet(f: DFile, start: int, end: int, repo: Path, new_rev: Optional[str], ui_lang: str = "en") -> str:
    S = strings_for(ui_lang)
    lang = lang_of(f.path)
    picked: list[tuple[tuple, Row]] = []
    covered = set()
    for r in f.rows:
        if r.kind == "del":
            if start <= r.pos_new <= end + 1:
                picked.append(((r.pos_new, 0, r.old), r))
        else:
            if start <= r.new <= end:
                picked.append(((r.new, 1, 0), r))
                covered.add(r.new)
    missing = [n for n in range(start, end + 1) if n not in covered]
    if missing:
        src = file_lines_new(repo, new_rev, f.path)
        if src:
            for n in missing:
                if 1 <= n <= len(src):
                    picked.append(((n, 1, 0), Row("ctx", None, n, n, src[n - 1])))
    picked.sort(key=lambda t: t[0])
    rows = [r for _, r in picked]
    colored = highlight_rows(rows, lang)
    body = "".join(render_row(r, colored[i], two_cols=False) for i, r in enumerate(rows))
    adds = sum(1 for r in rows if r.kind == "add")
    dels = sum(1 for r in rows if r.kind == "del")
    rng = f"{start}" if start == end else f"{start}–{end}"
    wrap_btn = (
        f'<button type="button" class="snip-wrap" title="{S["snippet_wrap_off"]}" aria-label="{S["snippet_wrap_off"]}" aria-pressed="true">'
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M3 12h15a3 3 0 1 1 0 6h-4M3 18h7"/>'
        '<path d="m16 16-2 2 2 2"/></svg></button>'
    )
    return (
        f'<p class="loc">{esc(f.path)} · {rng}'
        f'<a class="loc-jump" href="#{f.slug}">{S["view_full_diff"]}</a></p>'
        f'<div class="gh-diff"><div class="gh-diff-hd"><span class="gh-file">{esc(f.path)}</span>'
        f'{wrap_btn}{render_stat(adds, dels)}</div>'
        f'<div class="gh-diff-body"><table class="gh-diff-tbl">{COLS1}<tbody>{body}</tbody></table></div></div>'
    )


def expand_snippets(explain_html: str, files: list[DFile], repo: Path, new_rev: Optional[str], ui_lang: str = "en") -> str:
    by_path = {f.path: f for f in files}
    for f in files:
        if f.old_path:
            by_path.setdefault(f.old_path, f)

    def sub(m: re.Match) -> str:
        path, s, e = m.group(1).strip(), int(m.group(2)), int(m.group(3) or m.group(2))
        f = by_path.get(path)
        if not f:
            eprint(f"[warn] snippet file not found in diff: {path}")
            return f"<!-- snippet failed: {esc(path)} -->"
        return render_snippet(f, s, e, repo, new_rev, ui_lang)

    return SNIPPET_RE.sub(sub, explain_html)


# --------------------------------------------------------------------------- assemble
def fill(template: str, key: str, value: str) -> str:
    marker = f"<!-- @@{key} -->"
    if marker not in template:
        eprint(f"[warn] marker not found in template: {marker}")
    return template.replace(marker, value)


def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s).replace("  ", " ").strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".", help="Path to the git repository")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--commit", help="A single commit (compared against its parent)")
    g.add_argument("--range", dest="range_", help="A..B or 'A B'")
    g.add_argument("--worktree", action="store_true", help="Working tree vs. HEAD (including staged changes)")
    ap.add_argument("--list", action="store_true", help="Print only files/hunks/line ranges, then exit")
    ap.add_argument("--explain", help="HTML body for the explain tab (4 sections, including snippet placeholders)")
    ap.add_argument("--comments", help="comments.json")
    ap.add_argument("--meta", help="meta.json (title, lede, author, slug)")
    ap.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    ap.add_argument(
        "--lang", default="en",
        help=f"UI language for the generated page (default: en; supported: {', '.join(STRINGS)})",
    )
    ap.add_argument(
        "--out",
        help="Output path. Defaults to <gallery>/<project>/<branch>/"
        "YYYY-MM-DD-explanation-<slug>.html, where <gallery> is $EXPLAIN_DIFF_GALLERY "
        "if set, else ~/explain-diff-gallery (falls back to %%TEMP%% if that directory "
        "can't be created)",
    )
    ap.add_argument("--open", action="store_true", help="Open in a browser once finished")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    repo = Path(args.repo).resolve()
    if not git_ok(repo, "rev-parse", "--git-dir"):
        raise SystemExit(f"not a git repository: {repo}")

    # ---- Determine the diff target
    common = ["--no-color", "--unified=3", "-M", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/"]
    new_rev: Optional[str]
    if args.commit:
        sha = run_git(repo, "rev-parse", args.commit).strip()
        short = run_git(repo, "rev-parse", "--short", sha).strip()
        parent = run_git(repo, "rev-parse", f"{sha}^", check=False).strip() or EMPTY_TREE
        diff_args = ["diff", *common, parent, sha]
        new_rev = sha
        author, date = run_git(repo, "show", "-s", "--format=%an%n%ad", "--date=short", sha).splitlines()[:2]
        label = short
    elif args.range_:
        a, b = re.split(r"\.\.+|\s+", args.range_.strip(), maxsplit=1)
        diff_args = ["diff", *common, a, b]
        new_rev = b
        author = run_git(repo, "show", "-s", "--format=%an", b).strip()
        date = _dt.date.today().isoformat()
        label = f"{run_git(repo, 'rev-parse', '--short', a).strip()}..{run_git(repo, 'rev-parse', '--short', b).strip()}"
    else:
        diff_args = ["diff", *common, "HEAD"]
        new_rev = None
        author = run_git(repo, "config", "user.name", check=False).strip() or "worktree"
        date = _dt.date.today().isoformat()
        label = run_git(repo, "rev-parse", "--short", "HEAD").strip() + "+wt"

    files = parse_unified(run_git(repo, *diff_args))
    adds = sum(f.adds for f in files)
    dels = sum(f.dels for f in files)

    if args.list:
        print(f"{label} · {len(files)} files · +{adds} -{dels}")
        for f in files:
            print(f"\n{f.path}  [{f.status}{', binary' if f.binary else ''}]  +{f.adds} -{f.dels}"
                  f"{'  (generated → collapsed)' if is_generated(f.path, f.adds + f.dels) else ''}")
            for h in f.hunks:
                news = [r.new for r in h.rows if r.new is not None]
                olds = [r.old for r in h.rows if r.old is not None]
                rn = f"new {news[0]}-{news[-1]}" if news else "new -"
                ro = f"old {olds[0]}-{olds[-1]}" if olds else "old -"
                print(f"    @@ {ro} | {rn}  {h.ctx.strip()}")
        changed = adds + dels
        print(f"\ncomments: minimum {max(1, changed // 12)} · recommended max {max(3, changed // 6)} ({changed} lines changed), "
              f"at least 1 per hunk ({sum(len(f.hunks) for f in files)} hunks total)")
        return 0

    meta = json.loads(Path(args.meta).read_text("utf-8")) if args.meta else {}
    comments = load_comments(Path(args.comments) if args.comments else None, files)

    # ---- Density check
    changed = adds + dels
    minimum = max(1, changed // 12)
    if comments and len(comments) < minimum:
        eprint(f"[warn] {len(comments)} comments < minimum {minimum} ({changed} lines changed / 12)")
    ceiling = max(3, changed // 6)
    if len(comments) > ceiling:
        eprint(f"[warn] {len(comments)} comments > recommended max {ceiling} ({changed} lines changed / 6). "
               f"Merge neighboring lines so the total card height doesn't exceed the diff")
    for f in files:
        for h in f.hunks:
            if not any(r.rv for r in h.rows) and not any(c.dfile is f and c.start is None for c in comments):
                eprint(f"[warn] hunk with no comment: {f.path} @@ +{h.new_start},{h.new_len}")

    # ---- Render fragments
    S = strings_for(args.lang)
    explain_html = Path(args.explain).read_text("utf-8") if args.explain else "<!-- no explain body -->"
    explain_html = expand_snippets(explain_html, files, repo, new_rev, args.lang)
    diff_html = "\n".join(render_review_file(f, comments, args.lang) for f in files)
    cards_html = render_comment_cards(comments, args.lang)
    files_html = render_file_links(files)

    author = meta.get("author") or author
    title = meta.get("title") or S["default_title"](label)
    lede = meta.get("lede") or ""
    slug = meta.get("slug") or re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    stat_text = f"+{adds} −{dels} · {len(files)} files"
    badges = (
        '<span class="badge"><span class="dot"></span>explain diff</span>'
        f'<span class="badge"><span class="mono">{esc(label)}</span></span>'
        f'<span class="badge">{esc(author)}</span>'
        f'<span class="badge">{esc(stat_text)}</span>'
    )
    side_stat = (f'<b>{len(files)}</b> files · <span class="add">+{adds}</span> <span class="del">−{dels}</span>'
                 f' · {S["comments_stat_html"](len(comments))}')
    tb_stat = (f'<b>{len(files)}</b> files <span class="add">+{adds}</span> <span class="del">−{dels}</span>'
               f' · {S["comments_stat_html"](len(comments))}')
    shown = [f.path for f in files[:3]]
    more = S["and_more"](len(files) - 3) if len(files) > 3 else ""
    footer = f"{esc(label)} · {esc(' · '.join(shown))}{more} · {esc(date)}"

    tpl = Path(args.template).read_text("utf-8")
    tpl = fill(tpl, "LANG", esc(args.lang))
    tpl = fill(tpl, "DOCTITLE", esc(f"explain-diff · {strip_tags(title)}"))
    tpl = fill(tpl, "BADGES", badges)
    tpl = fill(tpl, "TITLE", title)
    tpl = fill(tpl, "LEDE", lede)
    tpl = fill(tpl, "EXPLAIN", explain_html)
    tpl = fill(tpl, "FILES", files_html)
    tpl = fill(tpl, "SIDESTAT", side_stat)
    tpl = fill(tpl, "TBSTAT", tb_stat)
    tpl = fill(tpl, "DIFF", diff_html)
    tpl = fill(tpl, "COMMENTS", cards_html)
    tpl = fill(tpl, "FOOTER", footer)

    if args.out:
        out = Path(args.out)
    else:
        out = default_gallery_out(repo, slug)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(tpl, "utf-8")
    print(f"{out}\n{len(files)} files · +{adds} -{dels} · {len(comments)} comments (minimum {minimum})")
    refresh = DEFAULT_OUT_DIR / "refresh-catalog.py"
    if DEFAULT_OUT_DIR.is_dir() and out.resolve().is_relative_to(DEFAULT_OUT_DIR.resolve()) and refresh.is_file():
        try:
            subprocess.run(
                [sys.executable, str(refresh)],
                cwd=str(DEFAULT_OUT_DIR),
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            pass
    if args.open:
        if os.name == "nt":
            os.startfile(str(out))  # type: ignore[attr-defined]
        else:
            webbrowser.open(out.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
