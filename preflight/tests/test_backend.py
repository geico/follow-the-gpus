"""The preflight targets must work with a backend override. This test runs the real tofu.

The backend is a `local` backend with a state path outside the stack folder. It is a real
backend and needs no cloud. A remote backend fails the same way when init skips the backend.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from ftg_preflight.terraform import Terraform

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("tofu") is None, reason="tofu is not installed")


def init_command(target: str, tf_var: str, tf_dir: Path) -> list[str]:
    """The `tofu init` line that `make <target>` would run, from `make -n`."""
    out = subprocess.run(
        ["make", "-n", target, "MISE_EXEC=", f"{tf_var}={tf_dir}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # The line is `tofu -chdir=... init ... >/dev/null` (MISE_EXEC is empty).
    line = next(x for x in out.splitlines() if "tofu -chdir=" in x)
    return shlex.split(line.split(">/dev/null")[0])


@pytest.mark.parametrize(
    ("target", "tf_var"),
    [("preflight/aws", "PREFLIGHT_AWS_TF_DIR"), ("preflight/azure", "PREFLIGHT_AZURE_TF_DIR")],
)
def test_preflight_init_reads_the_backend(tmp_path: Path, target: str, tf_var: str):
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "main.tf").write_text('variable "x" {\n  default = "hi"\n}\n')
    state = tmp_path / "x.tfstate"
    (stack / "backend_override.tf").write_text(
        f'terraform {{\n  backend "local" {{\n    path = "{state}"\n  }}\n}}\n'
    )
    state.write_text(
        '{"version": 4, "terraform_version": "1.12.0", "serial": 1, "lineage": "a",'
        ' "outputs": {}, "resources": [{"mode": "managed", "type": "terraform_data",'
        ' "name": "n", "provider": "provider[\\"terraform.io/builtin/terraform\\"]",'
        ' "instances": [{"schema_version": 0, "attributes": {"id": "1", "input": null,'
        ' "output": null, "triggers_replace": null}}]}]}'
    )
    subprocess.run(init_command(target, tf_var, stack), check=True, capture_output=True)
    tf = Terraform(str(stack))
    assert tf.var("x") == "hi"
    assert tf.has_resource("terraform_data.n")
