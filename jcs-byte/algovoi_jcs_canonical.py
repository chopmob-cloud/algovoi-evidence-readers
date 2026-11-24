"""A second implementation of the JCS byte oracle, from the specification text.

Reads one JSON value on stdin and writes its RFC 8785 canonical UTF-8 bytes to
stdout with no trailing newline. Exits 1 and writes nothing when the input is
not JCS input, or not admission-profile input under --profile bounded.

Written against RFC 8785 section 3 and RFC 7493 section 2, not against another
implementation, so that a disagreement with the corpus is a disagreement about
the text. The three things a naive serialiser gets wrong, and this one does not,
are the UTF-16 code-unit member order, the ECMAScript number form, and the
literal printing of every character outside the escape set.

  stdin -> canonical UTF-8 bytes, exit 0
  stdin -> nothing, exit 1     (refused)

Profiles:
  jcs      RFC 8785 exactly.
  bounded  the corpus's admission profile on top of it: integers only, within
           the IEEE-754 safe range; at most 128 nested containers; no Unicode
           noncharacter in a member name or a string value.
"""
import json
import sys
from decimal import Decimal

SAFE_INT = 2**53 - 1
MAX_DEPTH = 128

_ESC = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


class Refused(Exception):
    """Input that this profile does not print."""


def es6_number(x):
    """ECMAScript Number::toString for a float, per ECMA-262 7.1.12.1."""
    if x != x or x in (float("inf"), float("-inf")):
        raise Refused("non-finite")
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    dec = Decimal(repr(abs(x)))
    tup = dec.as_tuple()
    raw = "".join(str(c) for c in tup.digits)
    digits = raw.rstrip("0") or "0"
    exp = int(tup.exponent) + (len(raw) - len(digits))
    k = len(digits)
    n = exp + k
    if k <= n <= 21:
        out = digits + "0" * (n - k)
    elif 0 < n <= 21:
        out = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        out = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        mant = digits[0] + ("." + digits[1:] if k > 1 else "")
        out = mant + "e" + ("+" if e >= 0 else "-") + str(abs(e))
    return sign + out


class Num:
    """A number carried as its ECMAScript spelling, the form JCS prints."""

    __slots__ = ("text", "value")

    def __init__(self, text, value):
        self.text = text
        self.value = value


def _is_noncharacter(cp):
    return (cp & 0xFFFE) == 0xFFFE or 0xFDD0 <= cp <= 0xFDEF


def _string(s, bounded):
    parts = ['"']
    for ch in s:
        cp = ord(ch)
        if 0xD800 <= cp <= 0xDFFF:
            raise Refused("lone surrogate")
        if bounded and _is_noncharacter(cp):
            raise Refused("noncharacter")
        if ch in _ESC:
            parts.append(_ESC[ch])
        elif cp < 0x20:
            parts.append("\\u%04x" % cp)
        else:
            parts.append(ch)
    parts.append('"')
    return "".join(parts)


def _key_order(s):
    # UTF-16 code-unit order; big-endian bytes compare the same way.
    return s.encode("utf-16-be", "surrogatepass")


def _ser(v, bounded, depth):
    """depth is the number of containers already open around this value."""
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, str):
        return _string(v, bounded)
    if isinstance(v, Num):
        if bounded:
            if not v.text.lstrip("-").isdigit() or not -SAFE_INT <= int(v.text) <= SAFE_INT:
                raise Refused("not an admitted integer")
        return v.text
    if isinstance(v, dict):
        if depth + 1 > MAX_DEPTH:
            raise Refused("depth")
        out = []
        for key in sorted(v, key=_key_order):
            out.append(_string(key, bounded) + ":" + _ser(v[key], bounded, depth + 1))
        return "{" + ",".join(out) + "}"
    if isinstance(v, list):
        if depth + 1 > MAX_DEPTH:
            raise Refused("depth")
        return "[" + ",".join(_ser(x, bounded, depth + 1) for x in v) + "]"
    raise Refused("unsupported %r" % (type(v),))


def _pairs(pairs):
    seen = set()
    for k, _ in pairs:
        if k in seen:
            raise Refused("duplicate key")
        seen.add(k)
    return dict(pairs)


def canonical(raw, bounded=False):
    """raw: bytes as read from stdin. Returns bytes, or raises Refused."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused("input is not UTF-8")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_float=lambda s: Num(es6_number(float(s)), float(s)),
            parse_int=lambda s: Num(es6_number(float(s)), float(s)),
            parse_constant=lambda s: (_ for _ in ()).throw(Refused(s)),
        )
    except Refused:
        raise
    except Exception:
        raise Refused("input is not JSON")
    return _ser(value, bounded, 0).encode("utf-8")


def main(argv):
    bounded = "--profile" in argv and argv[argv.index("--profile") + 1:][:1] == ["bounded"]
    try:
        out = canonical(sys.stdin.buffer.read(), bounded=bounded)
    except Refused:
        return 1
    sys.stdout.buffer.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))