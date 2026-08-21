# Code-BT Source Audit

## Primary publication

- Siyang Zhang, Bin Li, Jingtao Qi, Xueying Wang, Fu Li, Jianan Wang, En Zhu,
  and Jinjing Sun, *Code-BT: A Code-Driven Approach to Behavior Tree Generation
  for Robot Tasks Planning with Large Language Models*, IJCAI 2025 Main Track,
  pages 8814--8822.
- DOI: <https://doi.org/10.24963/ijcai.2025/980>
- IJCAI record: <https://www.ijcai.org/proceedings/2025/980>
- Final paper: <https://www.ijcai.org/proceedings/2025/0980.pdf>

The publication is the only primary Code-BT artefact located in the source
audit completed on 2026-08-21. The IJCAI record provides the paper and BibTeX,
and the paper provides no repository, supplementary-material, data, release,
or software-license link. Exact-title, DOI, author, and distinctive-example
searches of public GitHub repository and code indexes did not locate an author
implementation. This does not prove that private or unindexed code does not
exist; it means that no public implementation can currently be pinned or
vendored.

## Method recoverable from the paper

The paper describes three phases:

1. An LLM selects condition and action functions from an abstract API library
   using the task description and returns the selection as JSON.
2. The LLM uses rules, the task, an input/output format, and one-shot examples
   to generate modular Python code over multiple rounds. Later rounds receive
   previous code and unused APIs. JSON-format, Python-syntax, and structural
   checks return feedback until the output is valid or a maximum is reached.
3. A deterministic converter parses the Python with the standard-library
   `ast` module, maps calls and `if`/`if not`/`else` control flow to action,
   condition, subtree, Sequence, and Fallback nodes, and emits an XML behaviour
   tree.

The disclosed code constraints limit terminal results to `True` or `False`,
use nested `if` or `if not` statements to encode node progression, and permit
only one lowest-indentation `if`/`else` structure per generated code block.
The paper evaluates 24 tasks derived from BEHAVIOR-1K with four LLMs and states
that Code-BT is not tied to one LLM, so the API-selection and code-generation
roles are candidate replaceable inference roles. A benchmark comparison still
has to hold every other method choice fixed.

## Missing reproduction inputs

The paper does not publish the exact prompts or one-shot examples, JSON keys or
schemas, 24 task identifiers, API library, validators, AST-to-XML algorithm,
XML dialect, behaviour-tree tick engine, maximum generation/feedback budgets,
model inference settings, simulator implementation, or evaluator prompt. It
mentions `py-trees` and BehaviorTree.CPP as background ecosystems rather than
pinning either as an implementation dependency.

Consequently there is no official source revision, dependency closure, or
software licence to adopt. A source-faithful drop-in is unavailable. Any local
implementation must be labelled as a clean-room, paper-derived variant and
must freeze every missing prompt, grammar, validator, budget, runtime choice,
capability binding, and deviation before its adapter can be admitted.

## AutoAdapter-Bench disposition

Code-BT remains in the reusable audit catalogue, not in a formal experiment.
No adapter or runtime is admitted. A later clean-room implementation must map
only public task information and the frozen public capability interface; it
must not expose reference-driver source, private Harness criteria, bindings,
guards, or calibration data. Formal B2 use additionally requires a separately
declared experiment, a fixed driver-interface-adapter combination, complete
interface-bound validation, and the trusted Direct-MuJoCo physical verdict.
