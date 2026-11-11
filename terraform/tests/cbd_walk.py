"""Reference walker for terraform/tests/create-before-destroy.test.sh.

Reads the HCL of one stack directory and fails closed: any text it cannot parse is an error,
never a silent skip. Usage: cbd_walk.py <terraform dir> [stack ...]
"""
import glob, os, re, sys

# A managed resource that a create_before_destroy resource reaches, that does not set the option
# itself. It is create-first by propagation, so it must never be replaced by a plan with its
# dependents still in use. Each entry needs a reason that holds today.
ALLOW = {
    "azure": {
        "azurerm_storage_container.code": "name is the constant \"code\"; no argument that a plan changes forces a replace",
        "azurerm_storage_account.data": "name is a prefix plus a hash of the subscription ID, so it is stable; no plan replaces it",
    },
    "aws": {
        "aws_s3_bucket.data": "name is the prefix plus random_id.bucket_suffix, which is stable; no plan replaces the bucket",
        "random_id.bucket_suffix": "has no keepers, so it changes only on an explicit taint or replace",
    },
}

TOP = ("resource", "data", "locals", "module", "variable", "output", "provider", "terraform", "moved", "import", "check", "removed", "ephemeral")
NAME = r"[A-Za-z_][\w-]*"


class Unparsable(Exception):
    pass


def mask(text, where):
    """Return the code of text: comments and literal string text become spaces (newlines stay),
    so braces and brackets in them cannot confuse the block walk. Interpolations (${...}, %{...})
    stay as code, because they hold references. Raise Unparsable on an unterminated construct."""
    out = []
    n = len(text)
    i = 0
    stack = []  # open interpolations: brace depth inside each

    def blank(c):
        return "\n" if c == "\n" else " "

    def template(i, close, heredoc_tag=None):
        """Consume template text from i until the closing quote (or the heredoc end line);
        returns the index after it. Appends masked output."""
        term = re.compile(r"[ \t]*" + re.escape(heredoc_tag or "") + r"[ \t]*(?=\n|$)")
        if heredoc_tag is not None:  # an empty heredoc ends on its first line
            m = term.match(text, i)
            if m:
                out.append(" " * (m.end() - i - 1) + ")")
                return m.end()
        while i < n:
            c = text[i]
            if heredoc_tag is None and c == "\\":
                out.append("  ")
                i += 2
                continue
            if heredoc_tag is None and c == close:
                out.append(" ")
                return i + 1
            if heredoc_tag is not None and c == "\n":
                out.append("\n")
                i += 1
                m = term.match(text, i)
                if m:
                    out.append(" " * (m.end() - i - 1) + ")")  # ")" closes the "(" of the opener
                    return m.end()
                continue
            if c in "$%" and text[i + 1:i + 3] == c + "{":
                out.append("   ")  # "$${" and "%%{" are escapes for literal text
                i += 3
                continue
            if c in "$%" and text[i + 1:i + 2] == "{":
                out.append("  ")
                i = code(i + 2, interp=True)
                continue
            out.append(blank(c))
            i += 1
        raise Unparsable("%s: unterminated string or heredoc" % where)

    def code(i, interp=False):
        depth = 0
        while i < n:
            c = text[i]
            if c == '"':
                out.append(" ")
                i = template(i + 1, '"')
                continue
            if c == "#" or text.startswith("//", i):
                while i < n and text[i] != "\n":
                    out.append(" ")
                    i += 1
                continue
            if text.startswith("/*", i):
                j = text.find("*/", i + 2)
                if j < 0:
                    raise Unparsable("%s: unterminated /* comment" % where)
                out.append("".join(blank(x) for x in text[i:j + 2]))
                i = j + 2
                continue
            m = re.compile(r"<<(-?)([A-Za-z_][\w]*)[ \t]*\n").match(text, i)
            if m:
                # "(" for the opener and ")" for the end line let locals_of see the heredoc as one
                # bracket pair, so a line inside it that holds only ${x} or %{ if } is not a local.
                out.append("(" + " " * (m.end() - i - 2) + "\n")
                i = template(m.end(), None, m.group(2))
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                if interp and depth == 0:
                    out.append(" ")
                    return i + 1
                depth -= 1
            out.append(c)
            i += 1
        if interp:
            raise Unparsable("%s: unterminated interpolation" % where)
        return i

    code(0)
    res = "".join(out)
    if len(res) != n:
        raise Unparsable("%s: internal error, masked text length differs" % where)
    return res


def blocks(code, orig, where):
    """Yield (header, body) for each top-level block of masked code. Fail closed on any other
    top-level text and on unbalanced braces."""
    i, n = 0, len(code)
    while True:
        m = re.compile(r"\s*").match(code, i)
        i = m.end()
        if i >= n:
            return
        m = re.compile(r"(%s)\b([^\n{=]*)\{" % "|".join(TOP)).match(code, i)
        if not m:
            raise Unparsable("%s: cannot parse top-level text: %r" % (where, code[i:i + 60]))
        depth, j = 1, m.end()
        while depth and j < n:
            depth += {"{": 1, "}": -1}.get(code[j], 0)
            j += 1
        if depth:
            raise Unparsable("%s: unbalanced braces in block %r" % (where, m.group(0)))
        yield m.group(1), orig[m.start(2):m.end(2)], code[m.end():j - 1], orig[m.end():j - 1]
        i = j


def locals_of(body, where):
    cur, depth, res = None, 0, {}
    for line in body.split("\n"):
        if depth == 0 and line.strip():
            m = re.match(r"\s*(%s)\s*=" % NAME, line)
            if not m:
                raise Unparsable("%s: cannot parse local: %r" % (where, line.strip()))
            cur = m.group(1)
            res[cur] = ""
        if cur:
            res[cur] += line + "\n"
        depth += sum(line.count(c) for c in "([{") - sum(line.count(c) for c in ")]}")
    if depth:
        raise Unparsable("%s: unbalanced brackets in locals" % where)
    return res


def load(stack_dir):
    nodes, cbd = {}, set()
    files = sorted(glob.glob(os.path.join(stack_dir, "*.tf")))
    if not files:  # a missing or empty stack directory would pass with no finding
        raise Unparsable("%s: no .tf files" % stack_dir)
    for f in files:
        where = os.path.relpath(f)
        orig = open(f, encoding="utf-8-sig").read()  # utf-8-sig drops a BOM
        code = mask(orig, where)
        for kind, rest, body, orig_body in blocks(code, orig, where):
            labels = re.findall(r'"([^"]*)"|(\S+)', rest)
            labels = [a or b for a, b in labels]
            if kind in ("resource", "data", "ephemeral"):
                if len(labels) != 2 or not all(re.fullmatch(NAME, x) for x in labels):
                    raise Unparsable("%s: cannot parse %s header: %r" % (where, kind, rest))
                key = ("" if kind == "resource" else kind + ".") + labels[0] + "." + labels[1]
                if kind == "resource":  # a resource depends on its provider block
                    ex = re.search(r"\bprovider\s*=\s*(%s\.%s)" % (NAME, NAME), body) or re.search(r"\bprovider\s*=\s*(%s)" % NAME, body)
                    body += "\nprovider." + (ex.group(1) if ex else labels[0].split("_")[0]) + "\n"
                nodes[key] = body
                if kind == "resource" and re.search(r"create_before_destroy\s*=\s*true", body):
                    cbd.add(key)
            elif kind == "module":
                if len(labels) != 1 or not re.fullmatch(NAME, labels[0]):
                    raise Unparsable("%s: cannot parse module header: %r" % (where, rest))
                nodes["module." + labels[0]] = body
            elif kind == "locals":
                for name, b in locals_of(body, where).items():
                    nodes["local." + name] = b
            elif kind == "provider":
                # A resource depends on its provider block, which can reference a resource. Whether
                # OpenTofu carries create_before_destroy through a provider is not settled, so
                # fail closed and follow the edge.
                if len(labels) != 1 or not re.fullmatch(NAME, labels[0]):
                    raise Unparsable("%s: cannot parse provider header: %r" % (where, rest))
                # Find a top-level alias attribute in the masked body, so a comment or a nested
                # key cannot supply it; read the quoted value from the same offsets in the
                # original text, after the blanks and comments that follow "=".
                alias = None
                depth, pos = 0, 0
                for line in body.split("\n"):
                    am = re.match(r"[ \t]*alias[ \t]*=", line) if depth == 0 else None
                    if am:
                        k = pos + am.end()
                        while k < len(body) and body[k] in " \t\n" and orig_body[k] != '"':
                            k += 1
                        alias = re.compile(r'"([^"]*)"').match(orig_body, k)
                        break
                    depth += sum(line.count(c) for c in "([{") - sum(line.count(c) for c in ")]}")
                    pos += len(line) + 1
                nodes["provider." + labels[0] + ("." + alias.group(1) if alias else "")] = body
            else:
                # variable, output, ...: not nodes, but their code can hold references
                # only to nodes that do not depend on them, so skipping them is sound.
                pass
    # A module reference that is not a node would be a miss.
    for k, body in nodes.items():
        for ref in re.findall(r"(?<![\w.-])module\.(%s)" % NAME, body):
            if "module." + ref not in nodes:
                raise Unparsable("%s: reference to unknown module.%s in %s" % (stack_dir, ref, k))
    return nodes, cbd


def main(argv):
    root = argv[1]
    stacks = argv[2:] or ["azure", "aws"]
    bad = 0
    for stack in stacks:
        try:
            nodes, cbd = load(os.path.join(root, stack))
        except Unparsable as e:
            print("FAIL %s: the walker cannot parse the HCL: %s" % (stack, e), file=sys.stderr)
            bad = 1
            continue
        refs = {k: set() for k in nodes}
        for k, body in nodes.items():
            for other in nodes:
                if other != k and re.search(r"(?<![\w.])" + re.escape(other) + r"(?![\w-])", body):
                    refs[k].add(other)
        for r in sorted(cbd):
            seen, todo = set(), [r]
            while todo:
                for d in refs[todo.pop()]:
                    if d not in seen:
                        seen.add(d)
                        todo.append(d)
            for d in sorted(seen):
                if d.startswith(("data.", "local.", "ephemeral.", "provider.")) or d in cbd:
                    continue
                allow = ALLOW.get(stack, {}).get(d)
                if allow:
                    print("ok   %s/%s reaches %s (allowed: %s)" % (stack, r, d, allow))
                else:
                    print("FAIL %s/%s sets create_before_destroy and depends on %s, which has a fixed name that a plan can replace; OpenTofu makes it create-first too" % (stack, r, d), file=sys.stderr)
                    bad = 1
    if not bad:
        print("ok   no create_before_destroy chain reaches an unlisted fixed-name resource")
    return bad


if __name__ == "__main__":
    sys.exit(main(sys.argv))
