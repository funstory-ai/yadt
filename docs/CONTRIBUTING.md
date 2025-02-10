## How to contribute to YADT

### **About Language**

- Issues can be in Chinese or English
- PRs are limited to English
- All documents are provided in English only

### **Did you find a bug?**

- **Ensure the bug was not already reported** by searching on GitHub under [Issues](https://github.com/funstory-ai/yadt/issues).

Please pay special attention to:

1. Known compatibility issues with pdf2zh - see [#20](https://github.com/funstory-ai/yadt/issues/20) for details
2. Reported edge cases and limitations from downstream applications - see [#23](https://github.com/funstory-ai/yadt/issues/23) for discussion

- If you're unable to find an open issue addressing the problem, [open a new one](https://github.com/funstory-ai/yadt/issues/new?template=bug_report.md). Be sure to include a **title and clear description**, as much relevant information as possible.

### **If you wish to request changes or new features**

- Suggest your change in the [Issues](https://github.com/funstory-ai/yadt/issues/new?template=feature_request.md) section.

### **If you wish to add more translators**

- This project is not intended for direct end-user use, and the supported translators are mainly for debugging purposes. Unless it clearly helps with development and debugging, PRs for directly adding translators will not be accepted.
- You can directly use [PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate) to get support for more translators.

### **If you wish to contribute to YADT**

> [!TIP]
>
> If you have any questions about the source code or related matters, please contact the maintainer at aw@funstory.ai .
> 
> You can also raise questions in [Issues](https://github.com/funstory-ai/yadt/issues).
> 
> You can contact the maintainers in the pdf2zh discussion group.
> 
> We welcome pull requests and will review your contributions.


1. Fork this repository and clone it locally.
2. Use `doc/deploy.sh` to set up the development  environment.
3. Create a new branch and make code changes on that branch. `git checkout -b feature/<feature-name>`
4. Perform development and ensure the code meets the requirements.

5. Commit your changes to your new branch.

```
git add .

git commit -m "<semantic commit message>"
```

5. Push to your repository: `git push origin feature/<feature-name>`.

6. Create a PR on GitHub and provide a detailed description.

7. Ensure all automated checks pass.

#### Basic Requirements

##### Workflow

1. Please create a fork on the main branch and develop on the forked branch.

- When submitting a Pull Request (PR), please provide detailed descriptions of the changes.

- If the PR fails automated checks (showing checks failed and red cross marks), please review the corresponding details and modify the submission to ensure the new PR passes automated checks.

2. Development and Testing

- Use the `uv run yadt` command for development and testing.

- Code formatting

3. Dependency Updates

- If new dependencies are introduced, please update the dependency list in pyproject.toml accordingly.

- It is recommended to use the `uv add` command for adding dependencies.

4. Documentation Updates

- If new command-line options are added, please update the command-line options list in README.md accordingly.

5. Commit Messages

- Use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), for example: feat(translator): add openai.

6. Coding Style

- Please ensure submitted code follows basic coding style guidelines.
- Use pep8-naming.
- Comments should be in English.
- Follow these specific Python coding style guidelines:

  a. Naming Conventions:

  - Class names should use CapWords (PascalCase): `class TranslatorConfig`
  - Function and variable names should use snake_case: `def process_text()`, `word_count = 0`
  - Constants should be UPPER_CASE: `MAX_RETRY_COUNT = 3`
  - Private attributes should start with underscore: `_internal_state`

  b. Code Layout:

  - Use 4 spaces for indentation (no tabs)
  - Maximum line length is 88 characters (compatible with black formatter)
  - Add 2 blank lines before top-level classes and functions
  - Add 1 blank line before class methods
  - No trailing whitespace

  c. Imports:

  - Imports should be on separate lines: `import os\nimport sys`
  - Imports should be grouped in the following order:
    1.  Standard library imports
    2.  Related third party imports
    3.  Local application/library specific imports
  - Use absolute imports over relative imports

  d. String Formatting:

  - Prefer f-strings for string formatting: `f"Count: {count}"`
  - Use double quotes for docstrings

  e. Type Hints:

  - Use type hints for function arguments and return values
  - Example: `def translate_text(text: str) -> str:`

  f. Documentation:

  - All public functions and classes must have docstrings
  - Use Google style for docstrings
  - Example:

    ```python
    def function_name(arg1: str, arg2: int) -> bool:
        """Short description of function.

        Args:
            arg1: Description of arg1
            arg2: Description of arg2

        Returns:
            Description of return value

        Raises:
            ValueError: Description of when this error occurs
        """
    ```

The existing codebase does not comply with the above specifications in some aspects. Contributions for modifications are welcome.

#### How to modify the intermediate representation

The intermediate representation is described by [il_version_1.rnc](https://github.com/funstory-ai/yadt/blob/main/yadt/document_il/il_version_1.rnc). Corresponding Python data classes are generated using [xsdata](https://xsdata.readthedocs.io/en/latest/). The files `il_version_1.rng`, `il_version_1.xsd`, and `il_version_1.py` are auto-generated and must not be manually modified.

##### Format RNC file

```bash
trang yadt/document_il/il_version_1.rnc yadt/document_il/il_version_1.rnc
```

##### Generate RNG, XSD and Python classes

```bash
# Generate RNG from RNC
trang yadt/document_il/il_version_1.rnc yadt/document_il/il_version_1.rng

# Generate XSD from RNC
trang yadt/document_il/il_version_1.rnc yadt/document_il/il_version_1.xsd

# Generate Python classes from XSD
xsdata generate yadt/document_il/il_version_1.xsd --package yadt.document_il
```
