from typer.testing import CliRunner

from factorlab.surfaces.cli.main import app
from _text import strip_ansi


runner = CliRunner()


def test_serve_help():
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--port" in strip_ansi(result.stdout)
