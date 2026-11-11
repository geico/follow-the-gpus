"""Fixtures for the create_before_destroy walker. Each hostile case must fail the
walker (a chain finding or a parse failure); each benign case must pass. Usage: <walker path>"""
import os, subprocess, sys, tempfile

walker = sys.argv[1]
CBD = 'resource "azurerm_storage_blob" "code" {\n  lifecycle { create_before_destroy = true }\n%s\n}\n'
FIXED = 'resource "azurerm_thing" "fixed" {\n  name = "x"\n}\n'
FIXED_H = 'resource "azurerm_thing" "fixed-name" {\n  name = "x"\n}\n'

def aliased(attrs, tail=""):  # an aliased provider that references a fixed-name resource
    return 'provider "azurerm" {\n' + attrs + '  name = azurerm_thing.fixed.id\n}\n' + CBD % '  provider = azurerm.other' + FIXED + tail

CASES = [  # (name, tf text, must fail)
    ("closing brace in a string", CBD % '  note = "}"\n  ref = azurerm_thing.fixed.id' + FIXED, True),
    ("unbalanced paren in a string in a local",
     'locals {\n  a = "("\n  b = azurerm_thing.fixed.id\n}\n' + CBD % '  ref = local.b' + FIXED, True),
    ("hyphenated resource name", CBD % '  ref = azurerm_thing.fixed-name.id' + FIXED_H, True),
    ("reference in a heredoc after a hash line",
     CBD % '  ref = <<EOT\n# not a comment\nx ${azurerm_thing.fixed.id}\nEOT' + FIXED, True),
    ("reference in a heredoc after a space and hash",
     CBD % '  ref = <<EOT\nx # ${azurerm_thing.fixed.id}\nEOT' + FIXED, True),
    ("module reference",
     CBD % '  ref = module.m.id' + 'module "m" {\n  source = "./m"\n  in = azurerm_thing.fixed.id\n}\n' + FIXED, True),
    ("reference to an unknown module", CBD % '  ref = module.nope.id', True),
    ("unterminated string", CBD % '  ref = "abc' + FIXED, True),
    ("unterminated block comment", CBD % '  /* open' + FIXED, True),
    ("unbalanced braces", CBD[:-2] + FIXED, True),
    ("text outside any block", 'foo = 1\n' + CBD % '' + FIXED, True),
    ("reference in a block comment is not a reference", CBD % '  /* azurerm_thing.fixed.id */' + FIXED, False),
    ("reference in a line comment is not a reference", CBD % '  # azurerm_thing.fixed.id' + FIXED, False),
    ("brace and hash in a plain string", CBD % '  note = "} # {"' + FIXED, False),
    ("escaped interpolation is literal", CBD % '  note = "$${azurerm_thing.fixed.id}"' + FIXED, False),
    # valid HCL that the walker used to reject
    ("locals heredoc with interpolation and directive lines",
     'locals {\n  a = <<EOT\n${var.x}\n%{ if true }\nyes\n%{ endif }\nEOT\n  b = 1\n}\n' + CBD % '' + FIXED, False),
    ("empty heredoc", CBD % '  note = <<EOT\nEOT' + FIXED, False),
    ("indented empty heredoc", CBD % '  note = <<-EOT\n  EOT' + FIXED, False),
    ("UTF-8 byte order mark", "\ufeff" + CBD % '' + FIXED, False),
    ("top-level ephemeral block",
     'ephemeral "azurerm_thing" "e" {\n  name = "x"\n}\n' + CBD % '' + FIXED, False),
    # cases that used to pass with a missed dependency
    ("reference after a minus sign",
     CBD % '  n = length(var.x)-azurerm_thing.fixed.count' + FIXED, True),
    ("reference through an ephemeral block",
     CBD % '  ref = ephemeral.azurerm_thing.e.id' + 'ephemeral "azurerm_thing" "e" {\n  name = azurerm_thing.fixed.id\n}\n' + FIXED, True),
    ("provider block that references a fixed-name resource",
     'provider "azurerm" {\n  name = azurerm_thing.fixed.id\n}\n' + CBD % '' + FIXED, True),
    ("aliased provider that references a fixed-name resource",
     'provider "azurerm" {\n  alias = "other"\n  name = azurerm_thing.fixed.id\n}\n' + CBD % '  provider = azurerm.other' + FIXED, True),
    # 7
    ("alias in a comment before the real alias",
     'provider "azurerm" {\n  # alias = "old"\n  alias = "other"\n  name = azurerm_thing.fixed.id\n}\n' + CBD % '  provider = azurerm.other' + FIXED, True),
    # the walker keyed the provider wrong, so the edge to provider.azurerm.other was missed
    ("alias after a block comment", aliased('  alias = /* c */ "other"\n', 'provider "azurerm" {\n  name = "x"\n}\n'), True),
    ("nested alias key before the real alias", aliased('  tags = {\n    alias = "bad"\n  }\n  alias = "other"\n'), True),
    ("hyphenated my-alias key before the real alias", aliased('  my-alias = "bad"\n  alias = "other"\n'), True),
    ("hyphen before module. is not a module reference",
     'locals {\n  a = 1\n}\n' + CBD % '  ref = local.a-module.x' + FIXED, False),
    ("provider block that references nothing", 'provider "azurerm" {\n  name = "x"\n}\n' + CBD % '' + FIXED, False),
]

bad = 0
for name, text, must_fail in CASES:
    with tempfile.TemporaryDirectory() as d:
        os.mkdir(os.path.join(d, "azure"))
        with open(os.path.join(d, "azure", "main.tf"), "w", encoding="utf-8") as f:
            f.write(text)
        r = subprocess.run([sys.executable, "-I", walker, d, "azure"], capture_output=True, text=True)
    failed = r.returncode != 0
    if failed == must_fail:
        print("ok   walker fixture: %s" % name)
    else:
        print("FAIL walker fixture: %s (rc=%d, expected %s)\n%s%s" % (name, r.returncode, "failure" if must_fail else "pass", r.stdout, r.stderr), file=sys.stderr)
        bad = 1

# a stack directory with no .tf file (missing, empty) must fail, not pass with no finding
for name, make_dir in (("missing stack directory", False), ("empty stack directory", True)):
    with tempfile.TemporaryDirectory() as d:
        if make_dir:
            os.mkdir(os.path.join(d, "azure"))
        r = subprocess.run([sys.executable, "-I", walker, d, "azure"], capture_output=True, text=True)
    if r.returncode != 0:
        print("ok   walker fixture: %s" % name)
    else:
        print("FAIL walker fixture: %s (rc=0, expected failure)\n%s%s" % (name, r.stdout, r.stderr), file=sys.stderr)
        bad = 1
sys.exit(bad)
