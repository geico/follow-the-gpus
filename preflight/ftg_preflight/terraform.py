"""Read the input variables and the state of a stack through the tofu CLI.

`tofu console` applies the defaults, terraform.tfvars, the variable files, and TF_VAR_* variables.
It reads no cloud API for a `var.<name>` expression.
`tofu state list` reads the state of the backend that is initialized, local or remote.
A required variable with no value prints `(known after apply)`. `var()` returns None for it.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass

Runner = Callable[..., subprocess.CompletedProcess]


class TerraformError(RuntimeError):
    pass


@dataclass
class Terraform:
    tf_dir: str
    binary: str = "tofu"
    run: Runner = subprocess.run

    def _call(self, args: list[str], stdin: str = "") -> subprocess.CompletedProcess:
        return self.run(
            [self.binary, f"-chdir={self.tf_dir}", *args],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )

    def var(self, name: str, *, required: bool = True) -> object:
        """The value of `var.<name>`. A missing optional variable gives None."""
        proc = self._call(["console"], stdin=f"jsonencode(var.{name})\n")
        if proc.returncode != 0:
            if not required and "undeclared input variable" in proc.stderr:
                return None
            raise TerraformError(f"tofu console failed for var.{name}: {proc.stderr.strip()}")
        return decode_console_json(proc.stdout)

    def vars(self, required: Iterable[str], optional: Iterable[str] = ()) -> dict[str, object]:
        values = {n: self.var(n) for n in required}
        values.update({n: self.var(n, required=False) for n in optional})
        return values

    def has_resource(self, address: str) -> bool:
        """True when the local state holds the resource. No state file gives False."""
        proc = self._call(["state", "list"])
        if proc.returncode != 0:
            return False
        return address in proc.stdout.split()


UNSET_MARK = "(known after apply)"


def decode_console_json(stdout: str) -> object:
    """`console` prints the result of jsonencode() as an HCL string, for example "\"eastus\"".

    A required variable with no value gives `(known after apply)`, which decodes to None.
    """
    text = stdout.strip().splitlines()[-1].strip()
    if text == UNSET_MARK:
        return None
    return json.loads(json.loads(text))
