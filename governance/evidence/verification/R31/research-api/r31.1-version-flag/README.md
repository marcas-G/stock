# R31.1 version --json 契约统一

修复前:
```
$ flab version --json
Error: No such option: --json  (rc=2)
```
修复后:
```
$ flab version --json
{"ok": true, "schema_version": 1, "command": "version", "data": {"version": "0.1.0"}, "artifacts": {}, "warnings": [], "error": null}
```
TDD: test_cli_research_version_accepts_json_flag 红(1 failed)→绿(21 passed)
