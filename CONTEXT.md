# explain-diff

A skill that turns a code change into an explanation page. The reader learns *why* it changed on
the Explain tab, and sees the actual diff alongside commentary on the Review tab.

## Language

**Explain tab**:
The long-form explainer page: Background · Intuition · Code · Quiz.
_Avoid_: content tab, main tab

**Review tab**:
The screen that expands every changed file as an accordion, with comment cards down the right.
_Avoid_: diff tab, inspection tab

**File header**:
The title bar of the accordion that expands/collapses one changed file on the Review tab. It sticks
below the review toolbar while that file is on screen, and gets pushed up when the next file's
header arrives.
_Avoid_: sticky header, file card

**Comment card**:
The single floating commentary panel on the right of the Review tab. It fills with whatever code
range sits at the vertical center of the screen (the first comment, initially), and sits next to
that range.
_Avoid_: review comment, PR comment

**Wrap**:
Whether a line of the Review-tab diff folds to the column width, or stays on one line with
horizontal scroll instead. Off (no folding) by default.
_Avoid_: line break, word wrap

**Reveal line**:
The horizontal line at the vertical center of the screen. On scroll-reveal, the comment for
whatever code crosses this line shows in the card. At the very top of the page it starts at the
first anchor and slides down to center.
_Avoid_: baseline, sticky line

**Scroll-reveal**:
The mode where a comment card opens automatically once its code crosses the reveal line.
_Avoid_: auto expand

**Click-reveal**:
The mode where crossing the reveal line does *not* open a card automatically — only clicking the
rail or the card itself opens one.
_Avoid_: manual expand
