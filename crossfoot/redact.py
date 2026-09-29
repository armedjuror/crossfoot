#!/usr/bin/env python3
"""
Redact personal data from a bank statement / card bill PDF so it can be shared
with an AI coding tool while building parsers.

TRUE redaction: text under each box is deleted from the file, not just covered.
After saving, the output is re-read; the script exits with an error if anything
it removed is still present.

Keeps (the parser needs these):
  dates, amounts, balances, column headers, layout geometry, bank codes (SBIN,
  HDFC, ...), IFSC codes, transaction types (UPI/DR, NEFT/CR, ...), narration
  remarks ("Yes Bank Credit Card", "Payment from PhonePe"), footers.

Removes:
  - Header values next to labels: customer name, address, email, phone,
    account number, customer ID, CKYC, PAN, nominee, date of birth.
  - Inside narrations: counterparty names, UPI IDs / account identifiers.
  - Anywhere: emails, UPI IDs, phone numbers, PAN, UAE IBAN / Emirates ID,
    full card numbers, account-like numbers above the table, any --terms.
  - Metadata, attachments, hidden text, and the password.

Reference numbers (UTR/RRN, 9+ digits) inside the table, via --references:
  fake (default)  random digits of the same length, same position
  keep            left as printed
  remove          deleted

Usage:
  pip install pymupdf
  python redact.py in.pdf out.pdf --password-prompt --dry-run     # see the plan first
  python redact.py in.pdf out.pdf --password-prompt
  python redact.py in.pdf out.pdf --terms-file terms.txt           # extra strings to remove
  python redact.py in.pdf out.pdf --box 1 30 80 300 140            # manual rectangle: page x0 y0 x1 y1

Never run this on held-out statements; they must never be shared with any tool.
Always open the output and look at every page before sharing it.
"""
import argparse
import getpass
import random
import re
import sys

import pymupdf

# ---------------------------------------------------------------- patterns

PATTERNS = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "upi_id": re.compile(r"[\w.\-]{2,}@[A-Za-z]{2,}"),
    "card_number": re.compile(r"\b(?:\d{4}[ -]){3}\d{4}\b"),
    "phone_in": re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)"),
    "phone_ae": re.compile(r"(?<!\d)(?:\+?971[\s-]?|0)5\d[\s-]?\d{3}[\s-]?\d{4}(?!\d)"),
    "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    "iban_ae": re.compile(r"\bAE\d{2}\s?(?:\d{4}\s?){4}\d{3}\b"),
    "emirates_id": re.compile(r"\b784-?\d{4}-?\d{7}-?\d\b"),
}
LONG_NUMBER = re.compile(r"(?<![\d.,])\d{9,18}(?![\d.,])")

# Never touched, even if a pattern elsewhere shares their characters.
AMOUNT_RE = re.compile(r"^[-(]?[\d,]*\d\.\d{2}\)?(?:Dr|Cr|DR|CR)?$")
DATE_RE = re.compile(r"^\d{1,2}[-/ .]?[A-Za-z]{3,9}[-/ .,]?\d{2,4}$"       # 01-Sep-2026, 01Sep26
                     r"|^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}$"                  # 01/09/2026
                     r"|^\d{4}-\d{2}-\d{2}$")                                # 2026-09-01

# Header labels whose values are personal. Matched against a line's text, lowercase.
PERSONAL_LABELS = [
    "customer name", "customer", "cust id", "customer no", "account holder", "name of the customer", "account name", "name",
    "communication address", "address", "email id", "email", "e-mail",
    "phone no", "mobile no", "mobile number", "phone", "mobile",
    "account no", "account number", "a/c no", "customer id", "cif", "ckyc id", "ckyc",
    "pan", "nominee", "nomination", "joint holder", "date of birth", "dob",
]
# Labels that are NOT personal, used only to know where a personal value ends.
OTHER_LABELS = [
    "account branch", "branch address", "branch", "ifsc", "micr", "account opening date",
    "account status", "account type", "currency", "statement period", "statement date",
]

# Narration pieces that are safe to keep.
KEEP_WORDS = {
    "UPI", "NEFT", "IMPS", "RTGS", "ATM", "POS", "NACH", "ACH", "ECS", "CHQ", "CHEQUE", "MMT",
    "DR", "CR", "TO", "BY", "FROM", "TRANSFER", "TRF", "PAYMENT", "PAY", "SALARY", "INT",
    "INTEREST", "CHG", "CHARGES", "CHRG", "GST", "IGST", "CGST", "SGST", "REFUND", "REV",
    "REVERSAL", "EMI", "LOAN", "CASH", "DEP", "DEPOSIT", "WDL", "WITHDRAWAL", "BIL", "BILL",
    "ONL", "ONLINE", "MB", "IB", "NET", "BANKING", "BANK", "SENT", "RECEIVED", "SELF", "INF",
}
BANK_CODES = {
    "SBIN", "HDFC", "ICIC", "UTIB", "KKBK", "YESB", "IDIB", "PUNB", "BARB", "CNRB", "UBIN",
    "FDRL", "IDFB", "INDB", "AUBL", "PYTM", "IBKL", "CITI", "SCBL", "HSBC", "KARB", "SIBL",
    "CSBK", "KVBL", "TMBL", "DBSS", "ESFB", "UJVN", "JAKA", "IOBA", "UCBA", "BKID", "MAHB",
    "PSIB", "CBIN", "RATN", "DLXB", "NSPB", "AIRP", "FINO", "JIOP",
}
NARRATION_START = re.compile(r"^(UPI|NEFT|IMPS|RTGS|NACH|ACH|ECS|MMT|INF|TRF|BIL|POS|ATM)\b", re.I)
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
NARRATION_HEADERS = {"particulars", "narration", "description", "details", "remarks", "transaction details"}


# ---------------------------------------------------------------- helpers

def words_of(page):
    return [(pymupdf.Rect(w[:4]), w[4]) for w in page.get_text("words")]


def lines_of(words, tol=3.0):
    """Group words into visual lines by vertical position."""
    lines = []
    for rect, text in sorted(words, key=lambda w: (round(w[0].y0), w[0].x0)):
        for line in lines:
            if abs(line[0][0].y0 - rect.y0) <= tol:
                line.append((rect, text))
                break
        else:
            lines.append([(rect, text)])
    return [sorted(l, key=lambda w: w[0].x0) for l in lines]


def find_table(words):
    """Return (header_top_y, header_bottom_y, narration_x_range) or (None, None, None)."""
    balance = [r for r, t in words if t.lower().strip(":") == "balance"]
    for b in balance:
        band = [(r, t) for r, t in words if r.y0 >= b.y0 - 14 and r.y1 <= b.y1 + 14]
        texts = {t.lower().strip(":") for _, t in band}
        if "date" in texts or texts & NARRATION_HEADERS:
            top = min(r.y0 for r, _ in band)
            bottom = max(r.y1 for r, _ in band)
            narr = next(((r, t) for r, t in band if t.lower().strip(":") in NARRATION_HEADERS), None)
            x_range = None
            if narr:
                left = max((r.x1 for r, _ in band if r.x1 <= narr[0].x0), default=0)
                right = min((r.x0 for r, _ in band if r.x0 >= narr[0].x1), default=10_000)
                x_range = (left, right)
            return top, bottom, x_range
    return None, None, None


class Faker:
    """Same original number -> same fake number, within one document."""
    def __init__(self):
        self.map = {}

    def __call__(self, value: str) -> str:
        if value not in self.map:
            fake = "".join(str(random.randint(0, 9)) if c.isdigit() else c for c in value)
            if value[:1].isdigit() and value[0] != "0" and fake[0] == "0":
                fake = str(random.randint(1, 9)) + fake[1:]
            self.map[value] = fake
        return self.map[value]


# ---------------------------------------------------------------- planners

class Plan:
    """Collects (label, rect, value, action) without duplicates; never touches protected rects."""
    def __init__(self, protected):
        self.items = []
        self.protected = protected

    def add(self, label, rect, value, action):
        if rect.is_empty or any(rect.intersects(p) for p in self.protected):
            return
        for _, r, _, _ in self.items:
            inter = rect & r
            if not inter.is_empty and inter.get_area() > 0.5 * min(rect.get_area(), r.get_area()):
                return
        self.items.append((label, rect, value, action))


def rect_of_substring(page, word_rect, word_text, sub):
    if sub == word_text:
        return word_rect
    hits = page.search_for(sub, clip=word_rect + (-1, -1, 1, 1))
    return hits[0] if hits else None


def plan_header_labels(page, words, table_top, plan):
    """Remove values that sit next to personal labels (name, address, ...)."""
    region = [(r, t) for r, t in words if table_top is None or r.y1 < table_top]
    lines = lines_of(region)
    label_hits = []   # (x0, y0, x1_of_label, personal?)
    for line in lines:
        text = " ".join(t for _, t in line).lower()
        matches = []
        for labels, personal in ((PERSONAL_LABELS, True), (OTHER_LABELS, False)):
            for lab in labels:
                for m in re.finditer(r"(?<![a-z])" + re.escape(lab) + r"(?![a-z])", text):
                    matches.append((m.start(), m.end(), personal))
        # "address" inside "branch address" is not a separate label
        matches = [m for m in matches
                   if not any(o is not m and o[0] <= m[0] and m[1] <= o[1] and (o[1] - o[0]) > (m[1] - m[0])
                              for o in matches)]
        for start, end, personal in matches:
            pos, first, last = 0, None, None
            for i, (_, t) in enumerate(line):
                ws, we = pos, pos + len(t)
                if first is None and we > start:
                    first = i
                if ws < end:
                    last = i
                pos = we + 1
            if first is None:
                continue
            label_hits.append((line[first][0].x0, line[first][0].y0, line[last][0].x1, personal))
    seen = []
    for h in label_hits:
        if not any(abs(h[0] - s_[0]) < 2 and abs(h[1] - s_[1]) < 2 for s_ in seen):
            seen.append(h)
    for x0, y0, lx1, personal in seen:
        if not personal:
            continue
        # value area: right of the label, until the next label to the right on the same row,
        # and down until the next label in the same column (max ~6 lines).
        right = min((s[0] for s in seen if s[0] > lx1 + 5 and abs(s[1] - y0) < 30), default=10_000)
        below = min((s[1] for s in seen if s[1] > y0 + 2 and abs(s[0] - x0) < 25), default=y0 + 72)
        for r, t in region:
            if r.y0 >= y0 - 2 and r.y0 < below - 2 and r.x0 > lx1 + 1 and r.x1 <= right + 1:
                if t in (":", "-") or "***" in t or re.search(r"[xX]{4,}", t):
                    continue          # separators, or already masked by the bank
                plan.add("header_value", r, t, "remove")


def plan_narrations(page, words, table_top, x_range, references, plan):
    """Parse each narration (UPI/.../...) and remove names and identifiers, fake references."""
    if table_top is None:
        return
    col = [(r, t) for r, t in words if r.y0 > table_top + 4 and
           (x_range is None or (x_range[0] - 1 <= r.x0 < x_range[1]))]
    lines = lines_of(col)
    date_ys = [r.y0 for r, t in words if r.y0 > table_top and DATE_RE.match(t)]
    has_date = lambda line: any(abs(y - line[0][0].y0) < 3 for y in date_ys)
    # Split into narrations (one per table row):
    #  - a line starting with UPI/NEFT/IMPS/... starts a new narration;
    #  - a line level with a date starts a new row if the current one already has its date
    #    (dates can be centred in a multi-line cell, so the first line may have none);
    #  - a big vertical gap (e.g. the footer) ends the table.
    chunks, current, last_y = [], None, None
    for line in lines:
        gap = last_y is not None and line[0][0].y0 - last_y > 30
        dated = has_date(line)
        if NARRATION_START.match(line[0][1]):
            current = {"lines": [line], "prefixed": True, "dated": dated}
            chunks.append(current)
        elif current is None or gap or (dated and current["dated"]):
            current = {"lines": [line], "prefixed": False, "dated": dated}
            chunks.append(current)
        else:
            current["lines"].append(line)
            current["dated"] = current["dated"] or dated
        last_y = line[0][0].y1
    for chunk in chunks:
        plan_one_narration(page, chunk["lines"], chunk["prefixed"], references, plan)


def plan_one_narration(page, chunk, prefixed, references, plan):
    # Build the narration string and remember which characters belong to which word.
    s, spans = "", []
    for line in chunk:
        for wi, (r, t) in enumerate(line):
            if s:
                if wi > 0:
                    s += " "
                elif not (s.endswith("/") or s.endswith("-") or t.startswith("/") or t.startswith("-")):
                    s += " "          # a wrapped line that doesn't break at a separator
            spans.append((len(s), len(s) + len(t), r, t))
            s += t
    sep = "/" if s.count("/") >= s.count("-") else "-"
    # segment boundaries
    segs, start = [], 0
    for i, ch in enumerate(s + sep):
        if ch == sep:
            segs.append((start, i))
            start = i + 1
    seen_name = False
    prev_kind = None
    for a, b in segs:
        raw = s[a:b]
        seg = raw.strip()
        if not seg:
            continue
        a += len(raw) - len(raw.lstrip())
        b = a + len(seg)
        up = seg.upper()
        if up in KEEP_WORDS or all(w in KEEP_WORDS for w in up.split()):
            kind, action = "type", "keep"
        elif re.fullmatch(r"[A-Za-z]{0,2}\d{9,22}", seg):
            kind, action = "reference", references
        elif "@" in seg:
            kind, action = "upi_id", "remove"
        elif up in BANK_CODES:
            kind, action = "bank_code", "keep"
        elif IFSC_RE.match(up):
            kind, action = "ifsc", "keep"
        elif prefixed and prev_kind == "bank_code":
            kind, action = "account_or_upi", "remove"     # the identifier that follows a bank code
        elif prefixed and any(c.isdigit() for c in seg):
            kind, action = "identifier", "remove"
        elif not prefixed:
            kind, action = "text", "keep"                  # plain narrations: only numbers/UPI IDs go
        elif not seen_name:
            kind, action = "name", "remove"
            seen_name = True
        else:
            kind, action = "remark", "keep"
        prev_kind = kind
        if action == "keep":
            continue
        for ws, we, r, t in spans:
            lo, hi = max(a, ws), min(b, we)
            if lo >= hi:
                continue
            part = t[lo - ws:hi - ws].strip()
            if not part:
                continue
            rect = rect_of_substring(page, r, t, part)
            if rect is not None:
                plan.add(kind, rect, part, action)


def plan_patterns(page, words, table_top, terms, references, plan):
    text = page.get_text("text")
    for label, rx in PATTERNS.items():
        for m in set(rx.findall(text)):
            for rect in page.search_for(m):
                plan.add(label, rect, m, "fake" if label == "card_number" else "remove")
    for r, t in words:
        for m in LONG_NUMBER.findall(t):
            if PATTERNS["phone_in"].fullmatch(m):
                continue
            rect = rect_of_substring(page, r, t, m)
            if rect is None:
                continue
            in_table = table_top is not None and rect.y0 > table_top
            plan.add("long_number", rect, m, references if in_table else "remove")
    for term in terms:
        if term.isdigit():
            # Number terms (PIN codes, short IDs) match whole numbers only,
            # so "673572" doesn't cut into a UTR that happens to contain it.
            rx = re.compile(r"(?<![\d.,])" + term + r"(?![\d.,])")
            for r, t in words:
                if rx.search(t):
                    rect = rect_of_substring(page, r, t, term)
                    if rect is not None:
                        plan.add("term", rect, term, "remove")
            continue
        for rect in page.search_for(term):
            plan.add("term", rect, term, "remove")


# ---------------------------------------------------------------- main

def leftovers(doc, removed_values, fakes, terms):
    found = []
    for page in doc:
        text = page.get_text("text")
        for label, rx in PATTERNS.items():
            if label == "card_number":
                continue
            for m in rx.findall(text):
                if not any(m in f for f in fakes):
                    found.append((page.number + 1, label, m))
        for v in removed_values:
            if len(v) < 3:
                continue
            pat = r"(?<![\w.,@])" + re.escape(v) + r"(?![\w@]|[.,]\d)"   # whole words, not inside amounts
            if re.search(pat, text):
                found.append((page.number + 1, "removed_value", v))
        low = text.lower()
        for term in terms:
            hit = (re.search(r"(?<![\d.,])" + term + r"(?![\d.,])", text) if term.isdigit()
                   else term.lower() in low)
            if hit and not any(term in f for f in fakes):
                found.append((page.number + 1, "term", term))
    return found


def clean_document(doc):
    """Strip metadata, attachments, hidden text, JavaScript. Bank PDFs often have
    slightly broken internal references that make scrub() fail; in that case rebuild
    the file's structure and retry, and as a last resort clean the parts directly.
    The leak check after saving runs either way (it also reads hidden text)."""
    try:
        doc.scrub()
        return doc
    except Exception as e:
        print(f"Note: scrub failed on the original structure ({e}); rebuilding and retrying.")
    rebuilt = pymupdf.open("pdf", doc.tobytes(garbage=4, clean=True, deflate=True))
    doc.close()
    try:
        rebuilt.scrub()
        return rebuilt
    except Exception as e:
        print(f"Note: scrub failed again ({e}); cleaning metadata and attachments directly.")
    rebuilt.set_metadata({})
    try:
        rebuilt.del_xml_metadata()
    except Exception:
        pass
    for name in list(rebuilt.embfile_names()):
        rebuilt.embfile_del(name)
    for page in rebuilt:
        for annot in list(page.annots() or []):
            page.delete_annot(annot)
    return rebuilt


def main(argv=None):
    ap = argparse.ArgumentParser(description="True redaction of personal data in statement PDFs.")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--terms", nargs="*", default=[], help="Extra strings to remove")
    ap.add_argument("--terms-file", help="File with one term per line")
    ap.add_argument("--references", choices=["fake", "keep", "remove"], default="fake",
                    help="Reference numbers inside the table (default: fake)")
    ap.add_argument("--box", nargs=5, action="append", type=float, metavar=("PAGE", "X0", "Y0", "X1", "Y1"),
                    help="Manual rectangle to remove, in PDF points; page is 1-based. Repeatable.")
    ap.add_argument("--password-prompt", action="store_true", help="Ask for the PDF password")
    ap.add_argument("--dry-run", action="store_true", help="List what would happen; write nothing")
    args = ap.parse_args(argv)

    terms = [t for t in args.terms if t.strip()]
    if args.terms_file:
        with open(args.terms_file, encoding="utf-8") as f:
            terms += [l.strip() for l in f if l.strip() and not l.startswith("#")]

    doc = pymupdf.open(args.input)
    if doc.needs_pass:
        pw = getpass.getpass("PDF password: ") if args.password_prompt else ""
        if not doc.authenticate(pw):
            sys.exit("Wrong or missing password (use --password-prompt).")
    if not any(page.get_text("text").strip() for page in doc):
        sys.exit("No text layer found: this looks like a scanned PDF. Redact it manually instead.")

    faker = Faker()
    removed_values, counts = set(), {}
    last_x_range = None
    for page in doc:
        words = words_of(page)
        protected = [r for r, t in words if AMOUNT_RE.match(t) or DATE_RE.match(t)]
        plan = Plan(protected)
        top, bottom, x_range = find_table(words)
        x_range = x_range or last_x_range
        last_x_range = x_range
        if top is None and page.number == 0:
            print("Note: no transaction table header found on page 1.")

        plan_header_labels(page, words, top, plan)
        plan_narrations(page, words, bottom, x_range, args.references, plan)
        plan_patterns(page, words, bottom, terms, args.references, plan)
        for b in args.box or []:
            if int(b[0]) - 1 == page.number:
                plan.add("manual_box", pymupdf.Rect(b[1:]), "", "remove")

        fakes = []
        for label, rect, value, action in plan.items:
            counts[f"{label}:{action}"] = counts.get(f"{label}:{action}", 0) + 1
            if args.dry_run:
                print(f"page {page.number + 1:>2}  {action:<6} {label:<15} {value!r}")
                continue
            if action == "keep":
                continue
            # Values re-checked after saving: identifiers anywhere, and names from narrations.
            # (A header address word like "KERALA" may legitimately appear elsewhere.)
            if value and (label in ("name", "account_or_upi", "identifier", "upi_id", "term")
                          or any(c.isdigit() for c in value) or "@" in value):
                removed_values.add(value)
            if action == "fake":
                page.add_redact_annot(rect, fill=(1, 1, 1))
                fakes.append((rect, faker(value)))
            else:
                page.add_redact_annot(rect, fill=(0, 0, 0))
        if args.dry_run:
            continue
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
        for rect, fake in fakes:
            page.insert_text((rect.x0, rect.y1 - rect.height * 0.22), fake,
                             fontsize=rect.height * 0.72, fontname="helv")

    print("Found:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing")
    if args.dry_run:
        return

    doc = clean_document(doc)
    doc.save(args.output, garbage=4, deflate=True, clean=True, encryption=pymupdf.PDF_ENCRYPT_NONE)
    doc.close()

    check = pymupdf.open(args.output)
    left = leftovers(check, removed_values, set(faker.map.values()), terms)
    meta = {k: v for k, v in check.metadata.items() if v and k not in ("format", "encryption")}
    check.close()
    if left or meta:
        for page_no, label, value in left:
            print(f"STILL PRESENT  page {page_no}  {label}: {value!r}")
        if meta:
            print("STILL PRESENT  metadata:", meta)
        sys.exit("Redaction incomplete. Do not share this file.")
    print(f"Saved {args.output}. Automatic check passed. Now open it and look at every page yourself.")


if __name__ == "__main__":
    main()