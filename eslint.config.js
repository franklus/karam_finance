const js = require("@eslint/js");
const { defineConfig, globalIgnores } = require("eslint/config");
const sonarjs = require("eslint-plugin-sonarjs");
const globals = require("globals");

const frappeGlobals = {
  $: "readonly",
  __: "readonly",
  cint: "readonly",
  cstr: "readonly",
  cur_dialog: "readonly",
  cur_frm: "readonly",
  cur_list: "readonly",
  cur_page: "readonly",
  cur_tree: "readonly",
  erpnext: "readonly",
  flt: "readonly",
  format_currency: "readonly",
  frappe: "readonly",
  get_field_obj: "readonly",
  hide_field: "readonly",
  jQuery: "readonly",
  locals: "readonly",
  moment: "readonly",
  refresh_field: "readonly",
  toggle_field: "readonly",
  unhide_field: "readonly",
  Vue: "readonly"
};

module.exports = defineConfig([
  globalIgnores([
    ".git/**",
    "**/boilerplate/**",
    "cypress/**",
    "node_modules/**",
    "**/public/dist/**",
    "**/public/js/lib/**",
    "**/templates/includes/**"
  ]),
  {
    linterOptions: {
      reportUnusedDisableDirectives: "error",
      reportUnusedInlineConfigs: "error"
    }
  },
  js.configs.recommended,
  sonarjs.configs.recommended,
  {
    files: ["**/*.{js,mjs,cjs}"],
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: "script",
      globals: { ...globals.browser, ...frappeGlobals }
    },
    rules: {
      "array-callback-return": ["error", { checkForEach: true }],
      camelcase: [
        "error",
        {
          allow: ["^[a-z]+(_[a-z]+)+$"],
          ignoreDestructuring: true,
          properties: "never"
        }
      ],
      complexity: ["error", 10],
      "consistent-return": "error",
      curly: ["error", "all"],
      "default-case-last": "error",
      "dot-notation": "error",
      eqeqeq: ["error", "always", { null: "ignore" }],
      "func-names": ["error", "as-needed"],
      "max-depth": ["error", 3],
      "max-len": ["error", { code: 88, ignoreComments: true, ignoreStrings: true }],
      "max-lines": ["error", { max: 250, skipBlankLines: true, skipComments: true }],
      "max-lines-per-function": [
        "error",
        { max: 60, skipBlankLines: true, skipComments: true, IIFEs: true }
      ],
      "max-nested-callbacks": ["error", 3],
      "max-params": ["error", 6],
      "max-statements": ["error", 25],
      "no-alert": "error",
      "no-await-in-loop": "error",
      "no-console": "error",
      "no-constructor-return": "error",
      "no-duplicate-imports": "error",
      "no-else-return": "error",
      "no-eval": "error",
      "no-implicit-coercion": "error",
      "no-implied-eval": "error",
      "no-loop-func": "error",
      "no-multi-assign": "error",
      "no-new-func": "error",
      "no-new-wrappers": "error",
      "no-param-reassign": [
        "error",
        {
          props: true,
          ignorePropertyModificationsFor: ["frm", "doc", "row"]
        }
      ],
      "no-plusplus": ["error", { allowForLoopAfterthoughts: true }],
      "no-promise-executor-return": "error",
      "no-restricted-globals": ["error", "event"],
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "CallExpression[callee.property.name='forEach'] > :matches(ArrowFunctionExpression, FunctionExpression)[async=true]",
          message:
            "Async forEach callbacks are not awaited; use a loop or aggregate the promises."
        }
      ],
      "no-return-assign": ["error", "always"],
      "no-script-url": "error",
      "no-self-compare": "error",
      "no-sequences": "error",
      "no-shadow": ["error", { hoist: "functions" }],
      "no-undef": "error",
      "no-unmodified-loop-condition": "error",
      "no-unreachable-loop": "error",
      "no-unused-vars": [
        "error",
        { args: "after-used", argsIgnorePattern: "^_", varsIgnorePattern: "^_" }
      ],
      "no-use-before-define": [
        "error",
        { classes: true, functions: false, variables: true }
      ],
      "no-useless-assignment": "error",
      "no-useless-call": "error",
      "no-useless-concat": "error",
      "no-useless-return": "error",
      "no-var": "error",
      "object-shorthand": ["error", "always"],
      "prefer-arrow-callback": "error",
      "prefer-const": "error",
      "prefer-numeric-literals": "error",
      "prefer-object-has-own": "error",
      "prefer-object-spread": "error",
      "prefer-promise-reject-errors": "error",
      "prefer-regex-literals": "error",
      "prefer-rest-params": "error",
      "prefer-spread": "error",
      "prefer-template": "error",
      quotes: ["error", "double", { avoidEscape: true }],
      radix: "error",
      "require-atomic-updates": "error",
      "require-await": "error",
      semi: ["error", "always"],
      "sonarjs/cognitive-complexity": ["error", 10],
      yoda: ["error", "never", { exceptRange: true }]
    }
  },
  {
    files: ["eslint.config.js"],
    languageOptions: {
      sourceType: "commonjs",
      globals: globals.node
    }
  },
  {
    files: ["**/*.bundle.js"],
    languageOptions: {
      globals: { global: "readonly", module: "readonly" }
    }
  },
  {
    files: ["**/*.cjs"],
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: "commonjs",
      globals: globals.node
    }
  },
  {
    files: ["tests/**/*.{js,mjs,cjs}", "**/*.test.{js,mjs,cjs}"],
    languageOptions: {
      sourceType: "commonjs",
      globals: globals.node
    },
    rules: {
      complexity: ["error", 15],
      "max-depth": ["error", 4],
      "max-lines-per-function": [
        "error",
        { max: 100, skipBlankLines: true, skipComments: true, IIFEs: true }
      ],
      "max-nested-callbacks": ["error", 4],
      "max-params": ["error", 8],
      "max-statements": ["error", 50],
      "no-console": "off",
      "sonarjs/code-eval": "off",
      "sonarjs/cognitive-complexity": ["error", 15],
      "sonarjs/no-empty-test-file": "off"
    }
  }
]);
