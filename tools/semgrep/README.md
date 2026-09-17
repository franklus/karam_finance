# Python module-size gate

`python_module_size.yml` is an unchanged copy of
`rules/custom/maintainability/python_module_size.yml` from
`vatsyy/frappe-semgrep-rules-extended`. The local copy lets pre-commit and CI
enforce the rule without requiring an unpublished revision of that repository.
Keep both copies identical when updating the rule; its fixtures and CLI tests
live in the rules repository.

Run `pre-commit run module-size --all-files`. CI's existing static-analysis job
runs this hook. It checks all Python files under `karam_finance` and `tools`,
including comments and blank lines, with a 1,000-line limit.

The hook disables file-size and ignore filters to retain the previous gate's
coverage. `--x-ignore-semgrepignore-files` is an internal Semgrep option, so the
hook pins Semgrep 1.176.0; rerun the rules repository's CLI tests when upgrading.
