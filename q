[33mcommit fcbf5f6b1f02d3e1cdecaf1eeb0c53d55999f209[m[33m ([m[1;36mHEAD[m[33m -> [m[1;32mmet-29/robustness-wiring[m[33m, [m[1;31morigin/MET-29-evaluation-robustness[m[33m)[m
Author: Ryan Lin <ryanh.lin@mail.utoronto.ca>
Date:   Thu Nov 19 13:25:09 2026 -0500

    Rebase from main

[33mcommit 1b17f2e53b8484660d68c3e75ffd66c389842476[m
Author: ryanh.lin@mail.utoronto.ca <119691704+PrimitiveTechExperience@users.noreply.github.com>
Date:   Wed Nov 18 22:57:31 2026 -0500

    More work on robustness

[33mcommit 7d5f5bf64d2b391b7aa97ab8f9344bd07d8e9f54[m
Author: Ryan Lin <ryanh.lin@mail.utoronto.ca>
Date:   Mon Nov 16 18:52:25 2026 -0500

    Robustness draft

[33mcommit 69fa6af3f1db445a4714715048f0246c63c0fb1b[m[33m ([m[1;31morigin/main[m[33m, [m[1;31morigin/HEAD[m[33m, [m[1;32mmain[m[33m)[m
Author: Julius Henriques <144366964+Julius14h@users.noreply.github.com>
Date:   Thu Nov 19 12:51:31 2026 -0500

    MET-45: Make Cisco's skill scanner an optional add-on, like Laya (#41)
    
    * MET-45: Make Cisco's skill scanner an optional add-on, like Laya
    
    metaskill's plugin.yaml declared cisco-ai-skill-scanner under
    python_dependencies, so enabling metaskill asked to install it, and
    declining left the whole plugin disabled: generalize and personalize
    could not be used without a scanner only evaluate needs.
    
    The scanner is now the optional metaskill-cisco add-on
    (plugins/metaskill-cisco), packaged like metaskill-laya: requires_plugins
    metaskill >=0.2.0 and python_dependencies cisco-ai-skill-scanner==2.2.1.
    It carries no code; installing it is what installs the scanner, and
    metaskill's adapter uses the scanner whenever it can import it. metaskill
    itself declares no Python dependencies.
    
    Runtime behaviour is unchanged: without the scanner, safety is scored by
    our own checks as partial and the dependency hint is not required. The
    install command everywhere is now
    `hermes plugins install <repo>#plugins/metaskill-cisco`, and the
    not-installed detail names the add-on.
    
    Docs: the safety design's decision 6 ("Cisco is a required dependency")
    is revised with the reason; README gains an optional add-on section next
    to Laya's; dependencies, cisco and score docs point at the add-on.
    
    Tests: tests/test_cisco_addon.py (metaskill declares nothing, the add-on
    pins TESTED_VERSION, requirements.txt matches, name and install command
    agree, it loads as a package without importing metaskill, the
    dependency hint names it); test_cisco's version check now reads the
    add-on's manifest. Hermes' own manifest scan parses the add-on like
    metaskill-laya.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * MET-45: Say how existing installs keep the scanner, and make the dependency guard strict
    
    Review on #41.
    
    - Hermes builds its environment with an exact `uv sync` from the enabled plugins' declarations,
      so packages nothing declares are removed. After `hermes plugins update metaskill`, an install
      without metaskill-cisco loses the scanner and Safety drops to `partial`. The README, the
      add-on's README and after-install.md now say to install the add-on when upgrading, and that
      turning it off takes effect when Hermes rebuilds its environment (restart Hermes).
    - test_metaskill_itself_declares_no_python_dependencies used a regex that missed flow-style
      lists, unquoted entries, pip_dependencies and a root pyproject.toml, all of which Hermes reads,
      so it was easy to fool. The guard is now blunt on purpose: any python_dependencies or
      pip_dependencies key counts unless its value is an empty list, so no spelling slips past it,
      and it needs no YAML parser (CI installs only pytest). It also asserts there is no root
      pyproject.toml. Tests pin each spelling it must catch and each manifest it must accept.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    ---------
    
    Co-authored-by: Claude Opus 5.5 <noreply@anthropic.com>

[33mcommit dc255aee4f3942d3d91fa878d3a363f68d2d9aa3[m
Author: Humungus Fungus <26120733+Humungus-Fungus@users.noreply.github.com>
Date:   Thu Nov 19 12:42:21 2026 -0500

    [MET-59] Collect Configuration Cleanliness evidence for arbitrary skills (#51)
    
    * [MET-55] Add deterministic Configuration Cleanliness checks
    
    * [MET-56] Report Configuration Cleanliness findings with partial coverage
    
    * [MET-56] Report pilot Separation and Integrity findings
    
    * [MET-56] Check required settings against usage evidence
    
    * [MET-56] Score confirmed Separation failures as zero
    
    * [MET-56] Score the reviewed Configuration Cleanliness reference
    
    * [MET-55] Address schema and reviewed-evidence feedback
    
    * [MET-56] Address evidence coverage and Cisco compatibility feedback
    
    * [MET-59] Collect configuration evidence from arbitrary skill bundles
    
    * [MET-59] Bound detector input before collecting evidence
    
    ---------
    
    Co-authored-by: Julius14h <juliusjhenriques@gmail.com>
    Co-authored-by: Claude Sonnet 5.5 <noreply@anthropic.com>

[33mcommit 86949a8a5d5fa5b6721dfee00bd3215b1575b0be[m
Author: Julius Henriques <144366964+Julius14h@users.noreply.github.com>
Date:   Thu Nov 19 12:25:05 2026 -0500

    MET-51: Record the deep safety tier's judge model and usage in the report (#44)
    
    * MET-44: Hermes agent adapter for the evaluate runner
    
    HermesAgent is the runner's Agent as a real Hermes session: model calls
    run on this machine with the user's provider, and every terminal and file
    tool call runs in the run's Docker sandbox container.
    
    Each run starts hermes_child.py on Hermes' own interpreter and bootstrap
    (from `hermes --print-runtime-command`). The child registers a Hermes
    terminal backend that docker-execs into the runner's container, binds a
    terminal scope selecting it, and builds an AIAgent with only the terminal
    and file toolsets. Usage maps through agent_run_from_hermes_usage; tool
    calls are counted from the session's messages, so efficiency's tool_calls
    signal is measured.
    
    A child process and a scope are needed because Hermes takes the backend
    from TERMINAL_ENV process-wide (a per-task override does not select it)
    and config.yaml overrides that variable. A prototype that relied on the
    override ran the agent's commands on the host. The child therefore
    refuses to run unless Hermes resolves our backend, and refuses to create
    any other backend, the local one included.
    
    Setup problems raise HermesAgentUnavailable (a JudgeError), which the
    runner records as an error rather than a failed run; agent failures and
    timeouts are failed runs. A timeout stops the child's process group.
    
    Unit tests need neither Hermes nor Docker. Opt-in integration tests
    (METASKILL_HERMES_IT=1, METASKILL_HERMES_LIVE=1) pass against Hermes
    0.21.5, Docker and a real model. Not yet wired into evaluate.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Address review: provider failures are errors, not judged runs
    
    When the model provider cannot serve a session (model unavailable,
    billing, retries exhausted, a content-policy block), Hermes returns
    normally with failed: true and its own error text as final_response.
    The adapter passed that text to the runner as the agent's answer, so
    every run failed (or a check graded the error text) and nothing showed
    as an error. Reproduced with a nonexistent model.
    
    The child now reports such a session as provider_error, keeping Hermes'
    error and failure_reason, and HermesAgent raises HermesAgentUnavailable,
    which the runner records as an error.
    
    Guard 2 also rebinds any module-level reference to the original
    _create_environment, so it no longer depends on Hermes importing it
    inside functions.
    
    Tests: a unit test for provider_error, and an integration test
    (METASKILL_HERMES_IT=1) with a model the provider does not have.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * MET-46: Run a task file in evaluate and score efficiency from the runs
    
    evaluate and rank take an optional task file (tasks_path argument,
    --tasks on the command, EvaluationConfig.tasks_path). Giving it is the
    opt-in to real model calls; without it nothing runs and evaluate behaves
    as before, with the task-based dimensions skipped.
    
    With a task file, evaluate loads it once (an unusable file is an error
    before anything runs), checks Docker with dependencies.py (a missing
    required dependency returns the list and runs nothing), creates the
    report folder, and runs each skill's tasks once through the runner with
    HermesAgent, every skill on the same tasks. Task-based dimensions
    (TASK_DIMENSIONS: quality, robustness, efficiency) get score_runs(skill,
    config, runs) with the skill's RunnerResult; efficiency implements it.
    Logs go into the report folder, the report holds every run as tests, and
    ReportMeta.agent_model records the model the sessions ran on.
    
    Runs the runner could not make are left out, never scored as the skill
    failing: the response gives their count and error type (the message can
    hold local paths and stays in the report), and a skill whose name cannot
    be a log folder still gets its other dimensions. The response adds
    task_runs.
    
    The schema stays under its 700-character budget: tasks_path and
    output_path carry no description, and the guidance (including that task
    runs cost model calls and need the user's OK) is in the guide skill.
    
    lift stays None until Quality, which defines it, is built.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Check the agent before running, and record the task runs' judge
    
    Two gaps in the task-run wiring:
    
    - A Hermes that could not be set up (missing, unsupported, a guard
      refusing) failed every run separately: tasks x runs x 2 errors. evaluate
      now calls the agent's model-free check() once, in a fresh workspace,
      after the Docker check; a problem is one error before anything runs.
      A check that hangs or loses Docker is an error too. check() keeps the
      child's stderr (it can name this machine's paths) off its message, on
      HermesAgentUnavailable.stderr, since this message reaches the agent.
    
    - The runner returns the judge of the task runs' LLM checks
      (judge_model, judge_usage), but evaluate set ReportMeta.judge_model to
      "" unconditionally. It now records the judge(s), and a new
      ReportMeta.judge_usage records their tokens and calls.
    
    Live: a broken Hermes launcher gives one error in about a second with
    nothing run; a working one passes the check and runs as before.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * MET-51: Record the deep safety tier's judge model and usage in the report
    
    At the deep tier, safety's two LLM passes (our review and Cisco's LLM
    analyzer) ran on the judge model, but the report header never said so:
    safety.score() returns only a DimensionScore, so the review Judge's
    judge_model/total_usage were dropped, and nothing aggregated the Cisco
    analyzer's per-call tokens. A deep-tier report showed judge_model "" and
    no usage.
    
    - DimensionScore gains optional judge_model and judge_usage, a generic
      channel any LLM-judged dimension fills (Clarity will reuse it).
    - The Cisco bridge records each call per scan in an LlmUsage (the
      provider/model that answered, tokens, retries included), passed
      through routed(); the existing API is unchanged.
    - safety.score() fills the fields from the review's Judge and Cisco's
      tracker. Both count for their whole life, so score() snapshots them
      before and after the scan and reports the difference: a caller that
      reuses backends across skills (backends_from_context) gets each
      skill's own calls, not a running total.
    - evaluate merges every dimension's judge with the task runs' judge
      (MET-46) into ReportMeta.judge_model / judge_usage.
    
    Static tier: both stay empty. A real deep-tier Cisco scan (the actual
    scanner and LiteLLM, a fake ctx.llm) records its call through the bridge
    into the score, per scan when the backend is reused.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Give RunnerResult the planned task ids, for task-based scorers
    
    Quality's assess() (#31) counts a task that never ran against the skill,
    so it needs the planned task ids rather than inferring the plan from the
    runs that happened. RunnerResult carried only tests and errors, so a
    score_runs(skill, config, runs) for Quality could not call assess()
    correctly. RunnerResult.task_ids now holds the loaded tasks' ids, in
    order, including tasks none of whose runs made it into tests; evaluate
    sets them too when a skill's name stops the runner before any run.
    
    With this, Quality's score_runs is one line:
    assess(runs.tests, task_ids=runs.task_ids,
           runs_per_task=config.runs_per_task,
           runner_errors=runs.errors).dimension_score
    
    Tests: the runner lists a task whose only runs could not be judged; a
    stand-in task dimension receives the planned ids through evaluate.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Say tasks_path is the opt-in to task runs, not to model calls
    
    Once Clarity (#45) lands, evaluate calls the judge for it on every run
    when Hermes has a model, and the deep safety tier already does. So
    "tasks_path is the opt-in to real model calls; without it nothing runs"
    is no longer true. It is the opt-in to task runs (agent sessions); the
    docs, config comment, module docstring and guide skill now say that, and
    that LLM-judged dimensions call the judge either way. As promised in the
    #45 review.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Score Quality from the task runs and put its lift in the report
    
    With #31 (Quality) on main, evaluate's task-run contract needed Quality's
    side: evaluate calls score_runs(skill, config, runs) for task-based
    dimensions, and quality.py had none, so any evaluation with a task file
    gave Quality "error: failed: AttributeError".
    
    - quality.assess_runs(config, runs) runs assess() over a RunnerResult:
      its tests, planned task_ids (so a task that never ran still counts)
      and errors. score_runs returns its DimensionScore.
    - evaluate copies the same assessment's lift into SkillResult.lift
      (_lift), so ranking's lift tie-break and the lift <= 0 warning work.
      It stays None without tasks or when Quality cannot compare, never an
      invented 0.
    - test_evaluate's no-tasks Quality check reads reason and detail: evaluate
      skips task dimensions itself, with the text in detail.
    
    Tests: both variants passing gives Quality 0.75 and lift 0.0; a skill
    that changes the outcome gives lift +1 and Quality 1.0 (provisional
    formula); runs that could not be made give Quality error and lift None.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * MET-44: Port the Hermes agent adapter to Hermes 0.23 and refuse tools outside the sandbox
    
    Hermes 0.22 moved `hermes -z` behind its gateway and removed
    hermes_cli.oneshot._resolve_model_and_provider, so every session on a current
    Hermes was a setup error. hermes_child.py now has its own copy of that choice,
    built on the helpers that remain in hermes_cli.model_switch and hermes_cli.models.
    
    Building the agent on 0.23.2 also showed the tool-search bridge (tool_search,
    tool_describe, tool_call), which reaches the user's connected apps and can install
    MCP servers, none of which runs in the sandbox. Both 0.21.5 and 0.23.2 have it.
    The child now turns the bridge off for its process and, after building the agent,
    refuses any tool outside the terminal and file toolsets (a setup error, fail closed).
    
    The transcript header reports the real version on 0.22 and later, where
    hermes_cli.__version__ is an install stamp.
    
    Tested on 0.21.5 (integration tests) and 0.23.2: tools reach the container, a full
    session against an offline fake provider runs a tool call in the sandbox, and a
    provider error comes back as unavailable, not a failed run.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    * MET-44: Count a failed session against the skill only when the task caused it
    
    Hermes marks a session `failed` for many reasons, and the child reported every one
    as a provider error, so the run was dropped. Some of them are task outcomes a skill
    can drive: a skill that floods the context gets its failing runs dropped instead of
    counted against it.
    
    The child now reads Hermes' failure_reason and turn_exit_reason. A reason in
    TASK_FAILURES (context_overflow, truncated, content_policy_blocked,
    context_compression_exhausted, context_compression_timeout) is a failed run, with
    the reason in the message. Any other reason, including one a later Hermes adds, stays
    an environment error and the run is not scored. loop_error stays out: it is an
    exception inside Hermes' own loop. content_policy_blocked is in because the skill's
    text can cause it; moving it is a one-line change.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    * MET-44: Make check() fail when the tools did not work
    
    check() returned ok whenever Hermes' setup was accepted. With Docker stopped, or on a
    Hermes whose tools run in another folder, every tool call failed and setup still
    succeeded, so #38's pre-flight would pass while the agent could do nothing.
    
    check_problems now reads each tool's answer: write_file wrote check.txt into the
    workspace root, terminal ran there and exited 0, read_file found what the terminal
    wrote, and every guard refused. Anything else raises HermesAgentUnavailable with a
    short reason per failure.
    
    Checked on Hermes 0.21.5, 0.22.2 and 0.23.2 against a real container (passes) and a
    container that does not exist (refused).
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    * MET-44: One copy rule for the runner and the skill message, the session's own seconds, test prerequisites
    
    Review items 5 to 7 on #36.
    
    - The skill message listed bundled files that only excluded symbolic links, while the
      runner also leaves out hard links and paths that resolve outside the skill, so the agent
      could be pointed at a file that is not in the sandbox. runner.copied(skill, rel) is now
      the one rule, used by both.
    - The runner timed agent.run, which includes the one to two seconds Hermes takes to start,
      and the child's own `seconds` was thrown away. AgentRun has an optional `seconds`;
      HermesAgent sets it from the child, and the runner prefers it when it is a non-negative
      number, falling back to its own clock otherwise.
    - Docs: the integration tests need Python 3.12.11, 3.13.4 or later (CVE-2025-4517) and the
      sandbox image pulled first. runner.md's AgentRun block now matches the class.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    * MET-44: Refuse Windows in HermesAgent until it is supported
    
    A session that times out is stopped with os.killpg and a process group of its own, which
    Windows does not have. The stop would raise, and the Hermes child would go on calling the
    model until max_iterations. run and check now raise HermesAgentUnavailable on Windows before
    anything starts, so the runner records an error, no model is called and no child is left.
    
    Supporting Windows is separate work (taskkill of the process tree, the /root working folder
    a reviewer saw there, portable tests, a Windows CI job). The adapter's own tests run as on
    Linux; the refusal has its own test.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    * MET-44: Require the success fields in the pre-flight check, even from an empty answer
    
    check_problems skipped validation when a tool's JSON parsed to {}, so write_file,
    terminal and read_file each answering {} passed. An unreadable answer is now None and
    a readable one, empty or not, is always checked.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    * MET-46: Refuse a task file inside an evaluated skill before any run
    
    The runner copies a skill's folder into /tmp/skill, so a tasks.json there handed the
    with-skill agent the checks and expected answers while the baseline had none. evaluate
    and rank now resolve the task file and its fixtures against every selected skill folder
    and refuse before the sandbox or agent is made.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    * MET-51: Report only the models this scan used when a backend is reused
    
    _judged kept the token usage per scan but took the model names from the backend's whole
    life, so a scan on model B after one on model A reported both. Judge and LlmUsage now
    count replies by provider/model, and a scan reports the models whose count grew.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    ---------
    
    Co-authored-by: Claude Opus 5.5 <noreply@anthropic.com>

[33mcommit 1ab61c9f2471c8bfedc8114ee39ca7595854ce18[m
Author: Julius Henriques <144366964+Julius14h@users.noreply.github.com>
Date:   Thu Nov 19 11:55:45 2026 -0500

    MET-46: Run a task file in evaluate and score efficiency from the runs (#38)
    
    * MET-44: Hermes agent adapter for the evaluate runner
    
    HermesAgent is the runner's Agent as a real Hermes session: model calls
    run on this machine with the user's provider, and every terminal and file
    tool call runs in the run's Docker sandbox container.
    
    Each run starts hermes_child.py on Hermes' own interpreter and bootstrap
    (from `hermes --print-runtime-command`). The child registers a Hermes
    terminal backend that docker-execs into the runner's container, binds a
    terminal scope selecting it, and builds an AIAgent with only the terminal
    and file toolsets. Usage maps through agent_run_from_hermes_usage; tool
    calls are counted from the session's messages, so efficiency's tool_calls
    signal is measured.
    
    A child process and a scope are needed because Hermes takes the backend
    from TERMINAL_ENV process-wide (a per-task override does not select it)
    and config.yaml overrides that variable. A prototype that relied on the
    override ran the agent's commands on the host. The child therefore
    refuses to run unless Hermes resolves our backend, and refuses to create
    any other backend, the local one included.
    
    Setup problems raise HermesAgentUnavailable (a JudgeError), which the
    runner records as an error rather than a failed run; agent failures and
    timeouts are failed runs. A timeout stops the child's process group.
    
    Unit tests need neither Hermes nor Docker. Opt-in integration tests
    (METASKILL_HERMES_IT=1, METASKILL_HERMES_LIVE=1) pass against Hermes
    0.21.5, Docker and a real model. Not yet wired into evaluate.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Address review: provider failures are errors, not judged runs
    
    When the model provider cannot serve a session (model unavailable,
    billing, retries exhausted, a content-policy block), Hermes returns
    normally with failed: true and its own error text as final_response.
    The adapter passed that text to the runner as the agent's answer, so
    every run failed (or a check graded the error text) and nothing showed
    as an error. Reproduced with a nonexistent model.
    
    The child now reports such a session as provider_error, keeping Hermes'
    error and failure_reason, and HermesAgent raises HermesAgentUnavailable,
    which the runner records as an error.
    
    Guard 2 also rebinds any module-level reference to the original
    _create_environment, so it no longer depends on Hermes importing it
    inside functions.
    
    Tests: a unit test for provider_error, and an integration test
    (METASKILL_HERMES_IT=1) with a model the provider does not have.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * MET-46: Run a task file in evaluate and score efficiency from the runs
    
    evaluate and rank take an optional task file (tasks_path argument,
    --tasks on the command, EvaluationConfig.tasks_path). Giving it is the
    opt-in to real model calls; without it nothing runs and evaluate behaves
    as before, with the task-based dimensions skipped.
    
    With a task file, evaluate loads it once (an unusable file is an error
    before anything runs), checks Docker with dependencies.py (a missing
    required dependency returns the list and runs nothing), creates the
    report folder, and runs each skill's tasks once through the runner with
    HermesAgent, every skill on the same tasks. Task-based dimensions
    (TASK_DIMENSIONS: quality, robustness, efficiency) get score_runs(skill,
    config, runs) with the skill's RunnerResult; efficiency implements it.
    Logs go into the report folder, the report holds every run as tests, and
    ReportMeta.agent_model records the model the sessions ran on.
    
    Runs the runner could not make are left out, never scored as the skill
    failing: the response gives their count and error type (the message can
    hold local paths and stays in the report), and a skill whose name cannot
    be a log folder still gets its other dimensions. The response adds
    task_runs.
    
    The schema stays under its 700-character budget: tasks_path and
    output_path carry no description, and the guidance (including that task
    runs cost model calls and need the user's OK) is in the guide skill.
    
    lift stays None until Quality, which defines it, is built.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Check the agent before running, and record the task runs' judge
    
    Two gaps in the task-run wiring:
    
    - A Hermes that could not be set up (missing, unsupported, a guard
      refusing) failed every run separately: tasks x runs x 2 errors. evaluate
      now calls the agent's model-free check() once, in a fresh workspace,
      after the Docker check; a problem is one error before anything runs.
      A check that hangs or loses Docker is an error too. check() keeps the
      child's stderr (it can name this machine's paths) off its message, on
      HermesAgentUnavailable.stderr, since this message reaches the agent.
    
    - The runner returns the judge of the task runs' LLM checks
      (judge_model, judge_usage), but evaluate set ReportMeta.judge_model to
      "" unconditionally. It now records the judge(s), and a new
      ReportMeta.judge_usage records their tokens and calls.
    
    Live: a broken Hermes launcher gives one error in about a second with
    nothing run; a working one passes the check and runs as before.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Give RunnerResult the planned task ids, for task-based scorers
    
    Quality's assess() (#31) counts a task that never ran against the skill,
    so it needs the planned task ids rather than inferring the plan from the
    runs that happened. RunnerResult carried only tests and errors, so a
    score_runs(skill, config, runs) for Quality could not call assess()
    correctly. RunnerResult.task_ids now holds the loaded tasks' ids, in
    order, including tasks none of whose runs made it into tests; evaluate
    sets them too when a skill's name stops the runner before any run.
    
    With this, Quality's score_runs is one line:
    assess(runs.tests, task_ids=runs.task_ids,
           runs_per_task=config.runs_per_task,
           runner_errors=runs.errors).dimension_score
    
    Tests: the runner lists a task whose only runs could not be judged; a
    stand-in task dimension receives the planned ids through evaluate.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Say tasks_path is the opt-in to task runs, not to model calls
    
    Once Clarity (#45) lands, evaluate calls the judge for it on every run
    when Hermes has a model, and the deep safety tier already does. So
    "tasks_path is the opt-in to real model calls; without it nothing runs"
    is no longer true. It is the opt-in to task runs (agent sessions); the
    docs, config comment, module docstring and guide skill now say that, and
    that LLM-judged dimensions call the judge either way. As promised in the
    #45 review.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Score Quality from the task runs and put its lift in the report
    
    With #31 (Quality) on main, evaluate's task-run contract needed Quality's
    side: evaluate calls score_runs(skill, config, runs) for task-based
    dimensions, and quality.py had none, so any evaluation with a task file
    gave Quality "error: failed: AttributeError".
    
    - quality.assess_runs(config, runs) runs assess() over a RunnerResult:
      its tests, planned task_ids (so a task that never ran still counts)
      and errors. score_runs returns its DimensionScore.
    - evaluate copies the same assessment's lift into SkillResult.lift
      (_lift), so ranking's lift tie-break and the lift <= 0 warning work.
      It stays None without tasks or when Quality cannot compare, never an
      invented 0.
    - test_evaluate's no-tasks Quality check reads reason and detail: evaluate
      skips task dimensions itself, with the text in detail.
    
    Tests: both variants passing gives Quality 0.75 and lift 0.0; a skill
    that changes the outcome gives lift +1 and Quality 1.0 (provisional
    formula); runs that could not be made give Quality error and lift None.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * MET-44: Port the Hermes agent adapter to Hermes 0.23 and refuse tools outside the sandbox
    
    Hermes 0.22 moved `hermes -z` behind its gateway and removed
    hermes_cli.oneshot._resolve_model_and_provider, so every session on a current
    Hermes was a setup error. hermes_child.py now has its own copy of that choice,
    built on the helpers that remain in hermes_cli.model_switch and hermes_cli.models.
    
    Building the agent on 0.23.2 also showed the tool-search bridge (tool_search,
    tool_describe, tool_call), which reaches the user's connected apps and can install
    MCP servers, none of which runs in the sandbox. Both 0.21.5 and 0.23.2 have it.
    The child now turns the bridge off for its process and, after building the agent,
    refuses any tool outside the terminal and file toolsets (a setup error, fail closed).
    
    The transcript header reports the real version on 0.22 and later, where
    hermes_cli.__version__ is an install stamp.
    
    Tested on 0.21.5 (integration tests) and 0.23.2: tools reach the container, a full
    session against an offline fake provider runs a tool call in the sandbox, and a
    provider error comes back as unavailable, not a failed run.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    * MET-44: Count a failed session against the skill only when the task caused it
    
    Hermes marks a session `failed` for many reasons, and the child reported every one
    as a provider error, so the run was dropped. Some of them are task outcomes a skill
    can drive: a skill that floods the context gets its failing runs dropped instead of
    counted against it.
    
    The child now reads Hermes' failure_reason and turn_exit_reason. A reason in
    TASK_FAILURES (context_overflow, truncated, content_policy_blocked,
    context_compression_exhausted, context_compression_timeout) is a failed run, with
    the reason in the message. Any other reason, including one a later Hermes adds, stays
    an environment error and the run is not scored. loop_error stays out: it is an
    exception inside Hermes' own loop. content_policy_blocked is in because the skill's
    text can cause it; moving it is a one-line change.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    * MET-44: Make check() fail when the tools did not work
    
    check() returned ok whenever Hermes' setup was accepted. With Docker stopped, or on a
    Hermes whose tools run in another folder, every tool call failed and setup still
    succeeded, so #38's pre-flight would pass while the agent could do nothing.
    
    check_problems now reads each tool's answer: write_file wrote check.txt into the
    workspace root, terminal ran there and exited 0, read_file found what the terminal
    wrote, and every guard refused. Anything else raises HermesAgentUnavailable with a
    short reason per failure.
    
    Checked on Hermes 0.21.5, 0.22.2 and 0.23.2 against a real container (passes) and a
    container that does not exist (refused).
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    * MET-44: One copy rule for the runner and the skill message, the session's own seconds, test prerequisites
    
    Review items 5 to 7 on #36.
    
    - The skill message listed bundled files that only excluded symbolic links, while the
      runner also leaves out hard links and paths that resolve outside the skill, so the agent
      could be pointed at a file that is not in the sandbox. runner.copied(skill, rel) is now
      the one rule, used by both.
    - The runner timed agent.run, which includes the one to two seconds Hermes takes to start,
      and the child's own `seconds` was thrown away. AgentRun has an optional `seconds`;
      HermesAgent sets it from the child, and the runner prefers it when it is a non-negative
      number, falling back to its own clock otherwise.
    - Docs: the integration tests need Python 3.12.11, 3.13.4 or later (CVE-2025-4517) and the
      sandbox image pulled first. runner.md's AgentRun block now matches the class.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    * MET-44: Refuse Windows in HermesAgent until it is supported
    
    A session that times out is stopped with os.killpg and a process group of its own, which
    Windows does not have. The stop would raise, and the Hermes child would go on calling the
    model until max_iterations. run and check now raise HermesAgentUnavailable on Windows before
    anything starts, so the runner records an error, no model is called and no child is left.
    
    Supporting Windows is separate work (taskkill of the process tree, the /root working folder
    a reviewer saw there, portable tests, a Windows CI job). The adapter's own tests run as on
    Linux; the refusal has its own test.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    * MET-44: Require the success fields in the pre-flight check, even from an empty answer
    
    check_problems skipped validation when a tool's JSON parsed to {}, so write_file,
    terminal and read_file each answering {} passed. An unreadable answer is now None and
    a readable one, empty or not, is always checked.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    * MET-46: Refuse a task file inside an evaluated skill before any run
    
    The runner copies a skill's folder into /tmp/skill, so a tasks.json there handed the
    with-skill agent the checks and expected answers while the baseline had none. evaluate
    and rank now resolve the task file and its fixtures against every selected skill folder
    and refuse before the sandbox or agent is made.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    ---------
    
    Co-authored-by: Claude Opus 5.5 <noreply@anthropic.com>

[33mcommit 9220c50b97557183525511fd1a3a69747f38b812[m
Author: Julius Henriques <144366964+Julius14h@users.noreply.github.com>
Date:   Thu Nov 19 11:47:51 2026 -0500

    MET-44: Hermes agent adapter for the evaluate runner (#36)
    
    * MET-44: Hermes agent adapter for the evaluate runner
    
    HermesAgent is the runner's Agent as a real Hermes session: model calls
    run on this machine with the user's provider, and every terminal and file
    tool call runs in the run's Docker sandbox container.
    
    Each run starts hermes_child.py on Hermes' own interpreter and bootstrap
    (from `hermes --print-runtime-command`). The child registers a Hermes
    terminal backend that docker-execs into the runner's container, binds a
    terminal scope selecting it, and builds an AIAgent with only the terminal
    and file toolsets. Usage maps through agent_run_from_hermes_usage; tool
    calls are counted from the session's messages, so efficiency's tool_calls
    signal is measured.
    
    A child process and a scope are needed because Hermes takes the backend
    from TERMINAL_ENV process-wide (a per-task override does not select it)
    and config.yaml overrides that variable. A prototype that relied on the
    override ran the agent's commands on the host. The child therefore
    refuses to run unless Hermes resolves our backend, and refuses to create
    any other backend, the local one included.
    
    Setup problems raise HermesAgentUnavailable (a JudgeError), which the
    runner records as an error rather than a failed run; agent failures and
    timeouts are failed runs. A timeout stops the child's process group.
    
    Unit tests need neither Hermes nor Docker. Opt-in integration tests
    (METASKILL_HERMES_IT=1, METASKILL_HERMES_LIVE=1) pass against Hermes
    0.21.5, Docker and a real model. Not yet wired into evaluate.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Address review: provider failures are errors, not judged runs
    
    When the model provider cannot serve a session (model unavailable,
    billing, retries exhausted, a content-policy block), Hermes returns
    normally with failed: true and its own error text as final_response.
    The adapter passed that text to the runner as the agent's answer, so
    every run failed (or a check graded the error text) and nothing showed
    as an error. Reproduced with a nonexistent model.
    
    The child now reports such a session as provider_error, keeping Hermes'
    error and failure_reason, and HermesAgent raises HermesAgentUnavailable,
    which the runner records as an error.
    
    Guard 2 also rebinds any module-level reference to the original
    _create_environment, so it no longer depends on Hermes importing it
    inside functions.
    
    Tests: a unit test for provider_error, and an integration test
    (METASKILL_HERMES_IT=1) with a model the provider does not have.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * MET-44: Port the Hermes agent adapter to Hermes 0.23 and refuse tools outside the sandbox
    
    Hermes 0.22 moved `hermes -z` behind its gateway and removed
    hermes_cli.oneshot._resolve_model_and_provider, so every session on a current
    Hermes was a setup error. hermes_child.py now has its own copy of that choice,
    built on the helpers that remain in hermes_cli.model_switch and hermes_cli.models.
    
    Building the agent on 0.23.2 also showed the tool-search bridge (tool_search,
    tool_describe, tool_call), which reaches the user's connected apps and can install
    MCP servers, none of which runs in the sandbox. Both 0.21.5 and 0.23.2 have it.
    The child now turns the bridge off for its process and, after building the agent,
    refuses any tool outside the terminal and file toolsets (a setup error, fail closed).
    
    The transcript header reports the real version on 0.22 and later, where
    hermes_cli.__version__ is an install stamp.
    
    Tested on 0.21.5 (integration tests) and 0.23.2: tools reach the container, a full
    session against an offline fake provider runs a tool call in the sandbox, and a
    provider error comes back as unavailable, not a failed run.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    * MET-44: Count a failed session against the skill only when the task caused it
    
    Hermes marks a session `failed` for many reasons, and the child reported every one
    as a provider error, so the run was dropped. Some of them are task outcomes a skill
    can drive: a skill that floods the context gets its failing runs dropped instead of
    counted against it.
    
    The child now reads Hermes' failure_reason and turn_exit_reason. A reason in
    TASK_FAILURES (context_overflow, truncated, content_policy_blocked,
    context_compression_exhausted, context_compression_timeout) is a failed run, with
    the reason in the message. Any other reason, including one a later Hermes adds, stays
    an environment error and the run is not scored. loop_error stays out: it is an
    exception inside Hermes' own loop. content_policy_blocked is in because the skill's
    text can cause it; moving it is a one-line change.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    * MET-44: Make check() fail when the tools did not work
    
    check() returned ok whenever Hermes' setup was accepted. With Docker stopped, or on a
    Hermes whose tools run in another folder, every tool call failed and setup still
    succeeded, so #38's pre-flight would pass while the agent could do nothing.
    
    check_problems now reads each tool's answer: write_file wrote check.txt into the
    workspace root, terminal ran there and exited 0, read_file found what the terminal
    wrote, and every guard refused. Anything else raises HermesAgentUnavailable with a
    short reason per failure.
    
    Checked on Hermes 0.21.5, 0.22.2 and 0.23.2 against a real container (passes) and a
    container that does not exist (refused).
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    * MET-44: One copy rule for the runner and the skill message, the session's own seconds, test prerequisites
    
    Review items 5 to 7 on #36.
    
    - The skill message listed bundled files that only excluded symbolic links, while the
      runner also leaves out hard links and paths that resolve outside the skill, so the agent
      could be pointed at a file that is not in the sandbox. runner.copied(skill, rel) is now
      the one rule, used by both.
    - The runner timed agent.run, which includes the one to two seconds Hermes takes to start,
      and the child's own `seconds` was thrown away. AgentRun has an optional `seconds`;
      HermesAgent sets it from the child, and the runner prefers it when it is a non-negative
      number, falling back to its own clock otherwise.
    - Docs: the integration tests need Python 3.12.11, 3.13.4 or later (CVE-2025-4517) and the
      sandbox image pulled first. runner.md's AgentRun block now matches the class.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    * MET-44: Refuse Windows in HermesAgent until it is supported
    
    A session that times out is stopped with os.killpg and a process group of its own, which
    Windows does not have. The stop would raise, and the Hermes child would go on calling the
    model until max_iterations. run and check now raise HermesAgentUnavailable on Windows before
    anything starts, so the runner records an error, no model is called and no child is left.
    
    Supporting Windows is separate work (taskkill of the process tree, the /root working folder
    a reviewer saw there, portable tests, a Windows CI job). The adapter's own tests run as on
    Linux; the refusal has its own test.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    * MET-44: Require the success fields in the pre-flight check, even from an empty answer
    
    check_problems skipped validation when a tool's JSON parsed to {}, so write_file,
    terminal and read_file each answering {} passed. An unreadable answer is now None and
    a readable one, empty or not, is always checked.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    ---------
    
    Co-authored-by: Claude Opus 5.5 <noreply@anthropic.com>

[33mcommit 98c186b688e53d19efa4d56d43bfac973ce8ed85[m
Author: Humungus Fungus <26120733+Humungus-Fungus@users.noreply.github.com>
Date:   Thu Nov 19 03:38:37 2026 -0500

    [MET-56] Report Configuration Cleanliness findings in evaluation (#49)
    
    * [MET-55] Add deterministic Configuration Cleanliness checks
    
    * [MET-56] Report Configuration Cleanliness findings with partial coverage
    
    * [MET-56] Report pilot Separation and Integrity findings
    
    * [MET-56] Check required settings against usage evidence
    
    * [MET-56] Score confirmed Separation failures as zero
    
    * [MET-56] Score the reviewed Configuration Cleanliness reference
    
    * [MET-55] Address schema and reviewed-evidence feedback
    
    * [MET-56] Address evidence coverage and Cisco compatibility feedback
    
    ---------
    
    Co-authored-by: Julius14h <juliusjhenriques@gmail.com>
    Co-authored-by: Claude Sonnet 5.5 <noreply@anthropic.com>

[33mcommit 0d06db54497d6b299d057360bcd124a4b46933e7[m
Author: Humungus Fungus <26120733+Humungus-Fungus@users.noreply.github.com>
Date:   Thu Nov 19 03:31:51 2026 -0500

    [MET-55] Add deterministic Configuration Cleanliness checks and fixtures (#48)
    
    * [MET-55] Add deterministic Configuration Cleanliness checks
    
    * [MET-55] Address schema and reviewed-evidence feedback

[33mcommit 12be002c98ed433ee316bd5707eb3a2c3b36c1a9[m
Author: Julius Henriques <144366964+Julius14h@users.noreply.github.com>
Date:   Thu Nov 19 01:00:37 2026 -0500

    MET-42: Keep the safety scan linear on hostile skills (#35)
    
    * MET-42: Keep the safety scan linear on hostile skills (closes #32)
    
    The own-checks scan was quadratic in more places than the paragraph
    analysis #32 describes: nearly every matcher re-read the rest of its line
    per match, so one long line of `rm -rf x`, `pip install`, `git push`,
    `print(` or similar stalled it for minutes to hours. SKILL.md has no size
    limit, so this was a denial of service against the scanner.
    
    Exact fixes (same results, linear cost):
    - Sentence boundaries, the confirmation window and the paragraph text are
      computed once per paragraph; matches look their sentence up by bisection.
    - print( calls are read once per line (_SecretView), falling back to the
      old per-call reading only where the two could differ.
    - Per-line memos for the stripped line, quotes and inline-code checks; the
      negation look-back is bounded; assignment regexes no longer backtrack
      across whitespace runs.
    
    Fail-closed limits (deterministic, so the same skill gives the same
    findings; a finding, not an error, so tripping one cannot drop this
    backend and leave the skill scored by Cisco alone):
    - Clearing prose matches as warnings is bounded per paragraph (200 matches,
      10,000-character sentences) and per skill (500,000 characters); past
      that, matches are reported.
    - A line with more than 200 read-ahead command starts, or a skill needing
      more than 1,000,000 characters of argument parsing, gets a CRITICAL
      scan-limit finding (new rule; CHECKS_VERSION 5).
    
    Local warnings look each place up once and share a 5 s limit, saying so
    when they stop early.
    
    On all 340 skills on disk, findings, delete targets, notes and deep
    re-check records are identical to main field for field; no limit fires;
    the scan is about 1.5% slower.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * MET-42: Test long benign prose against the line limit
    
    Review asked for a benign long-prose regression test next to the command-flood
    tests. Add one for a dense but realistic paragraph (under the limit), and one
    that pins the other side: a line of more than 200 command-like words is limited
    even when it reads as prose, since narrowing the match brings back the
    `.git push` bypass. Say in the constant's comment that ordinary English words
    count toward it.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    ---------
    
    Co-authored-by: Claude Opus 5.5 <noreply@anthropic.com>

[33mcommit 8b49451ec78c9c6dae83e677172d6f5112fe5f0f[m
Author: Ryan Lin <119691704+PrimitiveTechExperience@users.noreply.github.com>
Date:   Wed Nov 18 18:24:58 2026 -0500

    Met 26 evaluation clarity (#45)
    
    * Start on clarity - golden dataset plus some edits to scoring
    
    * Start of clarity
    
    * Clarity linked to Hermes CLI
    
    * Clarity Golden dataset realigned to current build of the quality branch
    
    * Doc fixes, and new criterion on clarity
    
    * added injection and reword cases
    
    * Some weird bug involving the hermes gateway not passing the relative folder back into its environment
    
    * Some more fixes involving faking scores, clarity breaking runs, and formatting.
    
    * Test suite fixes - placeholder fixtures
    
    * Addresing the Review + slow parsing + nested json crashes + weitd check passes when they should not.
    
    * Fixes to non-passing test cases
    
    ---------
    
    Co-authored-by: Claude Opus 5.5 <noreply@anthropic.com>

[33mcommit 7e5a1ca983a732cad56a62ddabef31aa7b340aac[m
Author: Humungus Fungus <26120733+Humungus-Fungus@users.noreply.github.com>
Date:   Wed Nov 18 11:50:32 2026 -0500

    [MET-24] Add golden evaluation pilot and provisional Quality scorer (#31)
    
    * Add golden pilot and quality/configuration metric designs
    
    * Fix pilot contracts and expand variant/profile coverage
    
    * Integrate golden pilot with existing evaluation runner
    
    * Add Quality scorer with provisional formula and tests
    
    * Remove extra trailing blank lines from evalutation pilot
    
    * Update Quality fixtures for split token metrics
    
    * Handle missing task evidence in Quality evaluation
    
    * Address golden pilot and Quality review feedback
    
    ---------
    
    Co-authored-by: Devarsh Devan <d.devan@mail.utoronto.ca>

[33mcommit 71066f80a532cfda8e830aaaac9a0d2618259549[m
Author: arora-jayant <81382724+arora-jayant@users.noreply.github.com>
Date:   Tue Nov 17 00:01:55 2026 -0500

    [MET-8] - Add reviewed detection ground truth for four real Hermes skills, with a scorer and integrity tests (#21)
    
    Co-authored-by: Cursor <cursoragent@cursor.com>

[33mcommit 4cd3608efa61124ba1d14e37a25fb7659624851a[m
Author: arora-jayant <81382724+arora-jayant@users.noreply.github.com>
Date:   Tue Nov 17 00:01:11 2026 -0500

    [MET-13] - Personalize a generalized skill from a validated config, with literal substitution, safe output paths and a value-free report (#33)
    
    Co-authored-by: Cursor <cursoragent@cursor.com>

[33mcommit 0243fc7572496be26188eb7bad2d3cd547a6b495[m
Merge: 8ce9691 3acc33e
Author: Manan Kakkar <94943688+MananKakkar1@users.noreply.github.com>
Date:   Thu Nov 12 20:07:21 2026 -0500

    Merge pull request #18 from CSC392-CSC492-Building-AI-ML-systems/feature/met-22-bundled-skill
    
    Bundle a metaskill:guide skill and trim the tool schema (MET-22)

[33mcommit 3acc33e835119f95c261e322fe40a33b9f420436[m[33m ([m[1;31morigin/feature/met-22-bundled-skill[m[33m)[m
Author: Manan <manan.kakkar.2005@outlook.com>
Date:   Thu Nov 12 20:06:22 2026 -0500

    Keep the registered guide description in sync with SKILL.md
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

[33mcommit 8ce96910836611778ae5ddd065da889f3b4f9e50[m
Merge: 84744f1 15ef2a1
Author: Manan Kakkar <94943688+MananKakkar1@users.noreply.github.com>
Date:   Thu Nov 12 20:06:15 2026 -0500

    Merge pull request #15 from CSC392-CSC492-Building-AI-ML-systems/laya-pii-companion-plugin
    
    Add the optional metaskill-laya add-on: Laya PII detection after the regex layer

[33mcommit 743b8f7648598482427532deeee69efa47d8c42c[m
Merge: 6bd0143 84744f1
Author: Manan <manan.kakkar.2005@outlook.com>
Date:   Thu Nov 12 19:59:38 2026 -0500

    Merge origin/main into feature/met-22-bundled-skill
    
    Keep the trimmed schema and add evaluate/rank to it and to the guide skill.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

[33mcommit 84744f1936545764f1491ada5ae9b95a3829b151[m
Author: Sohan <119457313+sohantati@users.noreply.github.com>
Date:   Thu Nov 12 16:16:12 2026 -0500

    Add skill inventory parser (#20)
    
    * Add skill inventory parser
    
    * Fix skill inventory parser review issues

[33mcommit 906dda818f010934ffef76bc1e174f7ff87085d0[m
Author: Julius Henriques <144366964+Julius14h@users.noreply.github.com>
Date:   Thu Nov 12 13:41:09 2026 -0500

    MET-25: Efficiency metrics plumbing and the efficiency scorer (#30)
    
    * Add efficiency metrics plumbing and the efficiency scorer
    
    Record the per-run usage the efficiency dimension needs. Metrics (and the
    AgentRun the agent fills) now carry input/output/reasoning tokens, the
    prompt-cache split, and llm_calls, with reproducible_input (input + cache,
    cache-agnostic) and total_tokens as properties. These thread through the
    runner, the report JSON and the log; rank shows total tokens and LLM calls.
    Wall-clock seconds, the cache split and cost are measured but not scored.
    
    Validated against a real `hermes -z --usage-file` run: every field
    populates (input/output/reasoning/cache tokens, api_calls, cost). That run
    surfaced reasoning_tokens (added) and host auxiliary usage (title
    generation etc.), which the AgentRun docstring now excludes from the score.
    
    Add efficiency.py: score_from_runs() scores cost relative to the baseline
    as the unweighted geometric mean of per-signal speedup ratios, squashed to
    [0, 1] and success-gated (a cheap failure earns no credit). The runner is
    not wired into evaluate yet, so the dimension entry point reports skipped;
    the logic is built and tested ahead of that integration, as safety's was.
    
    Design and backing (CVSS-style discipline: cite the method, own the
    policy) in docs/evaluate-efficiency.md.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    * Add a runner-to-efficiency integration test
    
    Drive run_tasks with a fake agent whose with-skill runs are cheaper and pass
    while the baseline is costlier and fails, then feed its TestResults straight
    into efficiency.score_from_runs. Asserts a real OK score above 0.5, proving
    the runner output and the scorer connect end to end in CI.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    * Address review: fix three scoring bugs and add a usage fixture
    
    score_from_runs had three ways a skill could get a score it did not earn:
    - A signal zero on either side was dropped, so adding tool calls against a
      zero baseline was free and removing them earned nothing. Now every signal
      uses an additive-smoothed ratio (b+1)/(w+1): defined at zero, ~b/w for
      large counts. A skill adding 50 tool calls vs 0 now scores ~0.27, not 0.5.
    - Gating was per run, so a task counted on its passing runs alone and the
      expensive failed runs were dropped. Now a task counts if any run passed and
      its cost averages ALL of its with-skill runs; two 20x failures plus one
      cheap pass now scores ~0.08, not 0.5.
    - `counted` incremented before a ratio was added. With smoothing every
      counted task contributes, and `detail` now reports "N of M passed tasks
      counted".
    
    Metrics.from_dict now ignores unknown/missing keys, so a report written by
    another version of the metrics still loads.
    
    Add agent_run_from_hermes_usage() mapping a `hermes -z --usage-file` report
    to an AgentRun (top-level counts, api_calls -> llm_calls, auxiliary excluded)
    with the real captured usage file as a fixture and a test, so "validated
    against a real Hermes run" is checkable and a field rename is caught. The
    demo script now reuses this mapper.
    
    Design doc sections 2-4 updated to match. Suite: 1146 passed, 19 skipped.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    * Address approval: fix two scoring errors and clamp Metrics
    
    - Reasoning tokens were double-counted. Hermes's normalize_usage already
      includes reasoning inside output_tokens (the completion/output count), so
      generation = output_tokens; reasoning_tokens stays as a sub-breakdown only.
      total_tokens drops reasoning too.
    - Unmeasured tool calls no longer score as "even". tool_calls is int | None;
      the usage-file mapper sets None (not reported), and the scorer leaves a None
      signal out of the geometric mean instead of smoothing a 0-vs-0 ratio of 1
      that diluted the measured signals toward 0.5.
    - Metrics.from_dict clamps counts to non-negative ints, so a malformed report
      cannot divide by zero; the doc notes the 2/3-not-1/2 smoothing trade-off.
    
    Merged main (MET-21 #11 and MET-31 #28 have landed; no conflicts). Suite:
    1281 passed, 9 skipped.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    ---------
    
    Co-authored-by: Claude Opus 4.8 <noreply@anthropic.com>

[33mcommit 6f274be79d83611a1fcdd7fc0d8476d3f788b1da[m
Author: Julius Henriques <144366964+Julius14h@users.noreply.github.com>
Date:   Thu Nov 12 13:25:36 2026 -0500

    MET-31: Back safety scores with CVSS bands and document the scale (#28)
    
    * Back safety scores with CVSS bands and document the scale
    
    Replace the placeholder SEVERITY_SCORE values with numbers derived from
    the CVSS v3.1 qualitative severity bands (FIRST.org). The scores are now
    computed from a _CVSS_BAND_CEILING table as 1 - ceiling/10, so the
    derivation is executable rather than hand-typed: CRITICAL 0.0, HIGH 0.11,
    MEDIUM 0.31, LOW 0.61, INFO 1.0 (INFO split from LOW). CRITICAL stays a
    hard 0 gate, which the band ceiling also yields.
    
    Add docs/safety/severity-scale.md explaining the full provenance: the
    cardinal scale (CVSS) and the ordinal ranking (NIST FIPS-199 magnitude +
    a recoverability principle), what is backed by a standard vs. owned
    policy, and how both map onto the code. Sync evaluate-safety.md section 5
    and safety/findings.md, and mark open question 3 decided.
    
    Tests assert against SEVERITY_SCORE instead of literals so the numbers
    live in one place. Safety suite: 310 passed, 10 skipped.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    * Close the safety severity-to-score open decision in the golden dataset
    
    The severity-to-score values are now decided (CVSS v3.1 bands; see
    docs/safety/severity-scale.md), so remove that half of the open-decision
    bullet. Scanner-category routing stays listed as open.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    * Address review: pin the scores, lower the warn threshold, sync docs
    
    - Add a test pinning SEVERITY_SCORE to its exact values, so a typo in
      _CVSS_BAND_CEILING or a wrong formula fails (the other tests reference the
      table, so nothing checked the values themselves).
    - Lower rank.py LOW_SAFETY from 0.5 to 0.3, so the "recommended skill, low
      safety" warning again means HIGH (0.11) or worse. MEDIUM is now 0.31, which
      would have tripped the old 0.5; a rerun shows the 4 MEDIUM skills (incl.
      arxiv's legitimate arXiv fetch) are exactly the ones this fixes.
    - Sync the docs that still had the old numbers: checks.md's severity table and
      its unpinned-install note, cisco.md's notice rationale (INFO is 1.0 now, not
      0.9) and its observed-scores list, review.md's and evaluate-safety.md's arxiv
      score (0.6 -> 0.31). Mark review.py as pending (MET-21) in severity-scale.md,
      and note that INFO = 1.0 (same as clean) is deliberate.
    
    Rerun of the 58 Hermes fixtures (static tier): 6 CRITICAL, 9 HIGH, 4 MEDIUM,
    5 LOW, 34 clean; 18 scores change, all within their tier, none newly warn
    after the 0.3 threshold. Suite: 1141 passed, 9 skipped.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    * Address approval nits: tie LOW_SAFETY to the table, fix docs
    
    - LOW_SAFETY is now read from SEVERITY_SCORE[Severity.MEDIUM] (via a lazy
      _low_safety() so ranking does not eagerly import the safety backends), so it
      tracks the CVSS band mapping and cannot silently stop warning on HIGH if the
      curve shape changes. Boundary test added: MEDIUM does not warn, HIGH does.
    - rank.md no longer says "below 0.5"; it says below a MEDIUM finding's score.
    - findings.md and severity-scale.md clarify that 1.0 also covers an INFO-only
      skill (INFO scores 1.0), not just "found nothing".
    - Fixed the findings.py links in severity-scale.md (relative to docs/safety/).
    
    Merged main (MET-21 #11 now landed); test_score.py already references
    SEVERITY_SCORE so the coordination resolved cleanly. Suite: 1264 passed, 9 skipped.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    ---------
    
    Co-authored-by: Claude Opus 4.8 <noreply@anthropic.com>

[33mcommit 4266e1f150f6af949a6830f2ba7f331fbf6870c1[m
Author: Julius Henriques <144366964+Julius14h@users.noreply.github.com>
Date:   Thu Nov 12 13:14:04 2026 -0500

    MET-21: Safety deep tier: LLM review backend (depends on #10) (#11)
    
    * MET-21: Run Cisco's LLM analyzer on the judge's model setting
    
    The Cisco adapter now passes judge.py's metaskill_judge task to ctx.llm,
    so Cisco and the LLM review always run on the same model, including
    when the user pins a judge model in config.yaml.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * MET-21: Expose skipped warnings as data for the review's re-check
    
    checks.skipped_warnings(skill) returns each prose match skipped as a
    warning with its location, rule, matched command, sentence, paragraph
    and the finding it would become if restored. Matches that were also
    confirmed are left out, since they would not be findings either way.
    Same parsing as scan(); the sentence logic is shared with the warning
    check.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * MET-21: Add the LLM review backend for the deep safety tier
    
    review.py is a thin layer over judge.py's Judge.review(): it supplies
    the four safety categories and severity guidance, points the model at
    the skill's stated purpose without copying the author's description
    into the trusted instructions, and turns judge outcomes into backend
    statuses.
    
    It also re-checks prose matches the own checks skipped as warnings, in
    one Judge.ask() call with two known-answer items mixed in under opaque
    ids. Verdicts can only restore a skipped match as a finding; a wrong
    known-answer item discards the batch. A failed re-check keeps the
    review's own findings and reports llm-unavailable.
    
    default_backends() adds the review at the deep tier only. review.md no
    longer restates the judge's behaviour; it links to judge.md instead.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * MET-21: Score malicious deletes of the user's personal files as CRITICAL
    
    Testing how a skill could delete the user's files and still score well
    found three gaps in the own checks:
    - ~/Documents, ~/Pictures and other personal folders were HIGH while
      ~/ was CRITICAL. They are now CRITICAL, under any home form.
    - The warning check skipped a match when its sentence contained any
      negation, so "Don't hesitate to run `rm -rf ~/Documents`" scored 1.0.
      The negation must now govern the command itself.
    - The confirmation check accepted the bare word, so "Confirm the export
      finished" or "# no need to confirm" cleared a delete. It now needs an
      actual step (asking the user, a prompt, a dry run, a backup), and
      ignores negated phrases.
    
    Every malicious variant tested now scores 0.0; genuine warnings and real
    confirmation steps still score 1.0. To keep false positives down,
    commands named as examples ("(`rm -rf`, etc.)") are skipped like
    warnings unless a delete has a concrete target. Abbreviations no longer
    end sentences. The review's CRITICAL guidance names personal files.
    CHECKS_VERSION is now 2.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * MET-21: Understand Python home-folder path expressions in deletes
    
    shutil.rmtree(os.path.expanduser("~/Documents")), os.path.join with
    os.environ["HOME"], Path.home() / "Documents" and Path(...).expanduser()
    were judged as unknown targets (HIGH), so a Python delete of the user's
    personal files scored 0.3 instead of 0.0. They are now resolved to ~/...
    paths, directly or through a variable assigned once.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * MET-21: Document how local warnings relate to the safety score
    
    The safety score is computed from what the skill says and is the same on
    every machine; a local warning says what it would do on this machine
    and never changes the score, even there. local.md now spells this out
    with an example, and the safety design gains open question 8: whether a
    local warning should also give a per-machine verdict or require the
    user's confirmation, without changing the shared score.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * MET-21: Bump CHECKS_VERSION to 3 after merging with judge.py
    
    PR #10 used version 2 for the hidden-characters rule, so this branch's
    rule changes become version 3. The 59 local Hermes skills give the same
    results with version 3 (42 none, 10 LOW, 7 HIGH, no hidden characters).
    
    review.md: the main review now sees hidden text marked as hidden rather
    than stripped; only the re-check strips it.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * MET-21: Close static-check bypasses flagged in review
    
    - _is_example only skips a bare destructive command (rm -rf, git reset
      --hard); a concrete operand -- a delete target, a force-push remote, a
      curl|bash URL, a named secret -- is an instruction wherever it appears,
      so example-wrapped curl|bash / force-push / echo-secret no longer slip
      through at the static tier.
    - _is_warning no longer lets a 'Warning:' label excuse an imperative to
      run the command, and 'why not run X' is no longer read as a negation.
    - Score rm -rf of ~/Library, ~/Movies and ~/OneDrive as CRITICAL too.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    * MET-21: Harden the deep-review warning re-check against injection
    
    - A wrong known-answer item now restores every match in the batch as a
      finding (possible injection) and flags the result, instead of leaving
      them skipped with a usable score.
    - Re-check the most severe matches first and flag the result when the cap
      leaves any un-re-checked, so padding cannot push a real match past it.
    - Cap each item's sentence and paragraph, so one huge prose unit cannot
      blow up the request and force the re-check to fail open.
    - Pick the two known-answer items at random from a pool and shuffle with
      SystemRandom, so the skill cannot work out which ids are the controls.
    - Report a degraded re-check as a static-equivalent fallback, not a
      completed deep tier.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    * MET-21: Run Cisco and the review on one model, even on old Hermes
    
    - default_backends and score take the registered auxiliary task; the
      caller passes None on a Hermes without register_auxiliary_task, so no
      unregistered task is sent to ctx.llm (matching Judge.from_context),
      instead of always sending JUDGE_TASK.
    - Forward judge_provider/judge_model to Cisco through routed(), so Cisco
      and the review share the model even when the user pins it through those
      config fields rather than the auxiliary task.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    * MET-21: Use relative imports in review.py (loads under any plugin name)
    
    review.py is new in this branch; keep it from reintroducing the absolute
    `from metaskill.evaluate...` imports that MET-40 removes from the rest of
    safety/, so the deep-review backend also loads when Hermes loads the
    package under a non-metaskill name.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    * MET-21: Rework the warning check to fix the false positives and bypasses
    
    The imperative verb-search from 36349e2 flagged genuine warnings as
    instructions ('Warning: using `rm -rf ~/Documents` deletes your files'
    scored CRITICAL). Replace it with the inverse rule Ryan suggested: a
    warning label marks a warning only when the sentence also describes the
    danger -- a negation, or harm words (deletes, permanently, overwrites,
    cannot be undone, gone, erase, ...). A label that merely points at the
    command ('Warning: next, `x`', 'Caution: enter `x`') is an instruction.
    Also squash whitespace so 'Why  not run' (two spaces) is caught.
    
    Fixes point C and four of the point-B rows. The two double-negative rows
    ('no reason not to run', 'silly not to run') need semantic understanding
    a regex can't give safely; tracked as a follow-up.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    * MET-21: Centre the re-check window on the match, close two fail-open gaps
    
    A (blocking): the re-check kept the first N characters of a match's
    sentence/paragraph, so a skill could pad before the command and push it
    (and the words that make it an instruction) out of what the model sees,
    reopening #3. Replace the head cap with a window centred on the match.
    
    Minors:
    - A real item the model returns no verdict for is now restored as a
      precaution and the result flagged, instead of staying skipped -- an
      injection could otherwise drop a match while answering both controls.
    - A match restored because the batch was untrusted (wrong control, or no
      verdict) now says so, rather than claiming the review confirmed it an
      instruction.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    * MET-21: Sync review.md, add backends_from_context (review minors + #5)
    
    - review.md matched the pre-fix behaviour: it still said a wrong control
      discards the batch and leaves matches skipped, and that controls cannot
      be told apart. Correct it to the restore-and-flag behaviour, the
      missing-verdict handling, the match-centred window, and note that the
      controls are recognisable by their text, so the random ids are a canary
      not a guarantee.
    - Add safety.backends_from_context(ctx, config): takes ctx.llm and the
      auxiliary task only where Hermes supports it, matching Judge.from_context,
      so whoever wires up score() can't forget to drop the task on old Hermes
      (review point #5).
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    * MET-21: Document the warning check's new rule and its static-tier limit
    
    Match checks.md to the reworked warning check: a label marks a warning
    only with danger wording, not on its own. Record the one known static
    gap -- double negatives like 'no reason not to run `x`' read as warnings
    but instruct the command, and a regex can't tell them from a genuine
    'be careful not to run `x`' without false-positiving on the common case.
    Left for the deep re-check, which restores them. Documented here rather
    than tracked as an issue, since there is no safe static-tier fix.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    * Address re-review: close the two CRITICAL-delete bypasses and harden the rest
    
    Blocking:
    1. Unrelated wording no longer counts as confirmation. _CONFIRM now requires
       "ask the user" to be about the action (before/to confirm/for confirmation),
       "back up" to take an object, and input() to have a yes/no prompt; `choice`
       matches the batch command (`choice /...`), not the English word. And a match
       cleared as confirmed is now routed to the deep re-check, so whatever the
       regex still gets wrong, the deep tier catches it.
    2. The re-check window centres on the match, not the command's first appearance.
       SkippedWarning carries the match offset in the sentence and paragraph, the
       window is cut there and then stripped of hidden characters. A command
       repeated in one sentence now shows each occurrence in its own item.
    
    Should fix:
    3. Harm words are searched with code spans masked (so `~/lost+found` does not
       trigger), `without`/`can't`/`won't` were dropped from them, and a list
       extends a governing negation only through a conjunction, not a bare comma.
    4. On a JudgeError (the model replied but the reply was unusable), the re-check
       restores the batch as untrusted instead of leaving warnings skipped; an
       outage (JudgeUnavailable) still keeps them skipped.
    5. The command snippet is stripped of hidden characters and capped at 200.
    
    Minor: _python_path unwraps str(...) and accepts `+ "/sub"` joins (str(Path.home())
    and expanduser("~") + "/Documents" now score CRITICAL); score() defaults task to
    None so a direct call never sends an unregistered task; hermes_bridge no longer
    shadows its own **kwargs.
    
    Merged main and resolved the CHECKS_VERSION collision: bumped to "4" (MET-39's
    frontmatter-aliases kept "3"), checks.md updated. Suite: 1252 passed, 9 skipped.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    * Address re-review: confirmation-specific re-check and _python_path hardening
    
    Blocking regression: a genuine confirmation became a finding at the deep
    tier, because confirmed matches were re-checked with the warning question
    ("instruction or warning?") and a real confirmation is an instruction. The
    deep re-check now asks confirmed/unconfirmed for confirmation matches and
    instruction/warning for warning matches -- two separate calls, each with its
    own known-answer controls and its own MAX_RECHECK_ITEMS budget. A
    confirmation match is restored only on `unconfirmed`, so a genuine
    confirmation stays cleared while a false one is caught. SkippedWarning
    carries a `kind`, and the restore message matches the question.
    
    Should fix (both from the _python_path minor):
    - A recursion depth limit (8) and a 500-char guard on the str()/`+` patterns,
      so str(str(...)) does not raise RecursionError and a long run of `+ 'a'`
      does not backtrack quadratically.
    
    Nits: the window strips hidden text first and then cuts on the match offset
    mapped into the stripped text, so a comment cannot be left half-shown;
    confirmation matches get their own re-check budget; the example tests assert
    severity. review.md documents the two-question re-check.
    
    Suite: 1255 passed, 9 skipped.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    * Address re-review: label+confirmation case, group isolation, padded paths
    
    - A genuine confirmation that also carries a warning label took the warning
      branch (kind="warning") and was restored as an instruction at the deep tier.
      The warning branch now uses kind="confirmation" when the match is also a
      confirmed destructive, so "Warning: … so ask the user before running
      `rm -rf ~/old`" stays cleared. Deep-tier test added.
    - The two re-check groups shared one failure path: if the confirmation call
      raised, the warning call's already-restored CRITICAL findings were discarded.
      Each group's call is now wrapped, so an earlier group's findings survive and
      the result is flagged.
    - Whitespace padding got past the path parsing (expanduser('~') + 520 spaces +
      "/Documents" scored HIGH). _python_path collapses whitespace runs first.
    
    review.md notes that the confirmation question, like the warning one, reads the
    skill's own text and so can be gamed by a claim of approval. Suite: 1260 passed.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    * Fix a ReDoS in the warning-governing regex (self-review)
    
    The nested quantifiers added in the fix for item 3 backtracked quadratically
    on hostile input: a long run of "`x`, " made _GOVERNING_NEGATION (and so the
    whole scan) hang. Bound the list repetition to {0,40} and let it take either a
    comma or a conjunction, which keeps every correctness case (single command,
    "or" lists, Oxford lists, and the bare-comma rejection) while making it linear
    -- 20k list items now match in ~0.03s. Found by running the adversarial inputs
    myself before re-review. Regression test added.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    * Post-approval follow-up: a third re-check verdict for warnings-as-confirmations
    
    A warning+confirmed match (routed to the confirmation question) that is really
    a warning -- it mentions a dry run or backup but says not to run the command --
    had no verdict for that and was answered `unconfirmed`, so it was restored as
    CRITICAL. Add a `not_instructed` verdict: the confirmation re-check now restores
    on `unconfirmed` and keeps on `confirmed` or `not_instructed`, with a third
    known-answer control. `_CheckConfig.keep_verdict` becomes `keep_verdicts`, and
    both comparisons in `_recheck_group` use it. Deep-tier test added:
    "Never run `rm -rf ~/`; to preview, use `rsync --dry-run`" stays cleared.
    
    Per the reviewer's prototype on the approval. Suite: 1262 passed, 9 skipped.
    
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
    
    ---------
    
    Co-authored-by: Claude Opus 5.5 <noreply@anthropic.com>

[33mcommit 4bc2c600913aa8c3038e93138f4d8b3f8065b762[m
Author: Ryan Lin <119691704+PrimitiveTechExperience@users.noreply.github.com>
Date:   Mon Nov 9 23:13:18 2026 -0500

    Met 39 evaluate connect evaluate and rank to the hermes plugin with unbuilt dimensions left null (#26)
    
    * Started the CLI linkage for eval and rank
    
    * Hermes CLI/metaskill evaluate (relative skill path) functions + forced cisco install
    
    * Some primitive outputs - might want to clean up before pr-ing
    
    * Shortened the output of the command
    
    * Fixes to some bugs in this branch

[33mcommit 15ef2a14a60d549d1197f88e3cb6019eb21e3fbc[m[33m ([m[1;31morigin/laya-pii-companion-plugin[m[33m)[m
Author: Manan <manan.kakkar.2005@outlook.com>
Date:   Fri Nov 6 20:11:52 2026 -0500

    Move the new README rows so this branch merges cleanly with the guide skill PR
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

[33mcommit 6bd0143bdb95cda2b577696f8ab64844525d5f7d[m
Author: Manan <manan.kakkar.2005@outlook.com>
Date:   Fri Nov 6 20:10:54 2026 -0500

    Address review of the guide skill: restore the privacy rule, keep registration data
    
    - Restore the rule that detected values are never returned or quoted to the
      tool description, since the guide is loaded only when the model chooses it.
    - Use one description for the registration and the SKILL.md frontmatter.
    - Retry register_skill with the description before the bare call.
    - Keep the path a Path: Hermes from April to September 2026 needs one.
    - Tell the agent not to claim success when status is partial.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

[33mcommit 298f2a18d91003256c7a06bd617405367523f7b3[m
Author: Manan <manan.kakkar.2005@outlook.com>
Date:   Sun Nov 1 16:16:15 2026 -0500

    Bundle a metaskill:guide skill and trim the tool schema (MET-22)
    
    Move the step-by-step "how to use metaskill" guidance out of the tool
    schema, which is sent on every turn, into a skill bundled with the plugin
    and loaded on demand with skill_view("metaskill:guide").
    
    - skills/guide/SKILL.md: resolve a skill name to its path with
      skill_view's skill_dir, confirm the path, pick generalize or
      personalize, find the user's config, call the tool, report counts only.
    - __init__.py: register the skill with ctx.register_skill, with a
      description for the <available_skills> index and requires_tools so it
      is hidden when the metaskill tool is unavailable. Guarded for Hermes
      versions without register_skill or without the description/frontmatter
      arguments.
    - schemas.py: trim descriptions and point to the guide. The schema goes
      from 219 to 152 tokens (o200k_base); the index line adds 31.

[33mcommit 5d083dde1200e59072e4220a9da46fd5c61ebc8e[m
Author: Manan <manan.kakkar.2005@outlook.com>
Date:   Fri Nov 6 20:10:52 2026 -0500

    Keep the setup skill's description on Hermes without frontmatter support
    
    Early register_skill versions take description but not frontmatter, and the
    fallback dropped both there. Try the middle signature before the bare call.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

[33mcommit 23c6eabbeb5916c52e8f2e12b5b5fff5b798fdc1[m
Merge: 8cc7c87 4179376
Author: Manan <manan.kakkar.2005@outlook.com>
Date:   Fri Nov 6 19:59:32 2026 -0500

    Merge origin/main into laya-pii-companion-plugin
    
    Keep both README install sections: the evaluate requirements from main and
    the optional Laya add-on from this branch.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

[33mcommit 8cc7c8746c5e537a229f8333608377e64e2eb925[m
Author: Manan <manan.kakkar.2005@outlook.com>
Date:   Fri Nov 6 19:59:20 2026 -0500

    Address review of the Laya add-on: refuse MPS, enforce the limit inside a file
    
    - Refuse mps and auto devices, since a hung device cannot be timed out.
    - Pass the deadline to detectors and check it between scoring batches.
    - Report generalize as partial until MET-12 writes a template.
    - Name the package that failed to import in the install hint.
    - Document that files are scored with a US region.
    - Make the layout test fast and deterministic without downloading the model.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

[33mcommit 417937671e7ffb4fc3d782278290538f2cae841b[m
Author: Julius Henriques <144366964+Julius14h@users.noreply.github.com>
Date:   Fri Nov 6 18:15:48 2026 -0500

    MET-40: Make safety imports relative so the plugin loads under any name (#23)
    
    The safety submodule was the only part of evaluate/ using absolute
    `from metaskill.evaluate...` imports; the rest already uses relative
    ones. Absolute imports need a top-level `metaskill` on sys.path, which
    a Hermes plugin loader does not provide when it loads the package under
    another name or by path, so importing safety raised ModuleNotFoundError.
    Convert the 15 internal imports in checks, cisco, findings, local and
    __init__ to relative (`..models`, `..judge`, `.checks`, ...). No
    behaviour change when run from the repo root; the module now also loads
    under a non-metaskill top-level name.
    
    Co-authored-by: Claude Opus 4.8 <noreply@anthropic.com>

[33mcommit 054b31d776d5c2fbd0a9aae6909bbd93e4e47b4b[m
Author: Ryan Lin <119691704+PrimitiveTechExperience@users.noreply.github.com>
Date:   Fri Nov 6 17:09:38 2026 -0500

    Strip hidden comments fix (#25)

[33mcommit 70effd99e8275c26a031423a5ca8f316b54da9d0[m
Author: Ryan Lin <119691704+PrimitiveTechExperience@users.noreply.github.com>
Date:   Fri Nov 6 00:01:53 2026 -0500

    Some minor correctness fixes from comment scrubbing, hidden chars not being stripped, doc misalignments and crashes on bad outputs (#22)

[33mcommit 264cce8ddb5b549d14948f3877a225e68ff4c881[m
Author: Ryan Lin <119691704+PrimitiveTechExperience@users.noreply.github.com>
Date:   Thu Nov 5 18:52:29 2026 -0500

    Met 35 evaluate implement ranking py and fix ranking system (#17)
    
    * Initial ranking implementation
    
    * Documentation changes + new open question about indicative placements
    
    * Several rouunds of bug fixes: judge clean, cleaned name, weight configs, doc changes, lift checks
    
    * Forgot to push the markdown escape change
    
    * Follow the team null policy in ranking
    
    Keep the 4/3/3/2/2/1 weights; take the rest of the policy from main.
    Any null dimension leaves a skill without overall, whatever its weight.
    Unranked skills are no longer given a mean over their scored dimensions,
    are ordered by name, and no winner is named when every skill is incomplete.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Rebase, and some notational fixes in the docs plus some more fixes to odd bugs
    
    ---------
    
    Co-authored-by: Claude Opus 5.5 <noreply@anthropic.com>

[33mcommit a52a2957afc11df7b077ce9bcea85b59838b2f9f[m
Author: Ryan Lin <119691704+PrimitiveTechExperience@users.noreply.github.com>
Date:   Thu Nov 5 14:23:24 2026 -0500

    Met 20 evaluation implement runner py (#16)
    
    * judge.py skeleton
    
    * Hermes integration - accidental other ai
    
    * Unified interface - might change later down the line
    
    * Some more docs
    
    * judge.py skeleton
    
    * Hermes integration - accidental other ai
    
    * Unified interface - might change later down the line
    
    * Some more docs
    
    * Uniting the conflicting judge implementation in this file and safety
    
    * test issue fix
    
    * Forgot to update some of the docs
    
    * Adds runner.py, tasks.py
    
    * judge.py skeleton
    
    * test issue fix
    
    * Adds runner.py, tasks.py
    
    * MET-20: Remove lines duplicated by the merge, fix runner test quote
    
    The merge of the pre-rebase branch (ff8538c) duplicated lines in
    __init__.py, docs/safety/review.md and tests/test_tool_routing.py.
    Restore them to the rebased versions (7d6f531).
    
    test_llm_judge_is_built_once_and_reports_its_model quoted "wrote it",
    which main's judge.py now rejects as too short (MIN_QUOTE_WORDS /
    MIN_QUOTE_CHARS). Use a longer agent output and quote.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Temporary Docker sandbox
    
    * Added docker deependency requirement to evaluate
    
    * fixed intellisense type issues in tests.
    
    * Some more work on docker integration - resizable volumes, network restrictions, custom backend for flags, timeouts
    
    * More work - runner copying into docker, plus some variance reduction
    
    * skill size and skill files issues
    
    * Some formatting fixes, and symlink test skip on windows
    
    * Fixed issues regarding exit code timeouts, small bug fixes, oversized skills
    
    ---------
    
    Co-authored-by: Claude Opus 5.5 <noreply@anthropic.com>

[33mcommit 8ba3b7993a3e5a89250d83f24763378a8bf98456[m
Author: Julius Henriques <144366964+Julius14h@users.noreply.github.com>
Date:   Tue Nov 3 23:01:33 2026 -0500

    Add the 58 Hermes skills as shared test fixtures (#13)
    
    The skills bundled with Hermes Agent (NousResearch/hermes-agent, skills/,
    commit 78c7b9fda, MIT), copied unchanged into tests/fixtures/hermes-skills
    with Hermes's LICENSE and a SOURCE.md. They are shared real-skill test
    data for every component; tests/hermes_skills.py loads them without
    PyYAML. Two repository files that are not skills were left out.
    
    The first user is a safety snapshot: tests/safety/test_hermes_skills.py
    runs the own checks on all 58 skills and compares each skill's findings
    with tests/safety/hermes_expected.json, so a rule change that adds or
    removes a finding on a real skill fails CI until the snapshot is
    refreshed on purpose (tests/safety/update_hermes_snapshot.py).
    
    tests/conftest.py makes tests/ importable for the shared helpers and
    stops pytest collecting the test files some fixture skills ship.
    
    Co-authored-by: Claude Opus 5.5 <noreply@anthropic.com>

[33mcommit 658a4e6cf1e26bac256dae22895f550f4cac5192[m
Author: Humungus Fungus <26120733+Humungus-Fungus@users.noreply.github.com>
Date:   Tue Nov 3 22:53:17 2026 -0500

    Add golden dataset docs (#6)
    
    * Add golden dataset docs
    
    * Point the scoring doc at the evaluation designs on main
    
    The evaluate-design-docs branch has been merged, so the authoritative
    evaluation design is now evaluate.md and evaluate-safety.md on main, and
    the transformation design is design.md.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Map golden dataset safety evidence to the safety categories
    
    Gold labels need the same categories the evaluator reports, so the
    safety evidence list now maps each kind of evidence to a category from
    evaluate-safety.md section 2. Embedded secrets belong to Configuration
    Cleanliness, and path validation is marked open since no category
    covers it yet.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Address review feedback on golden dataset and scoring docs
    
    ---------
    
    Co-authored-by: Devarsh Devan <d.devan@mail.utoronto.ca>
    Co-authored-by: Julius14h <juliusjhenriques@gmail.com>
    Co-authored-by: Claude Opus 5.5 <noreply@anthropic.com>

[33mcommit beeb74b7cb9e29ca1dbc09fd2644523ca0f1dee1[m
Author: Sohan <119457313+sohantati@users.noreply.github.com>
Date:   Tue Nov 3 21:17:35 2026 -0500

    Add config template structure (#19)
    
    Co-authored-by: tatisoha <s.tati@mail.utoronto.ca>

[33mcommit f8b77939bd2f08e190e33fad263bc3a201305c90[m
Author: Manan <manan.kakkar.2005@outlook.com>
Date:   Mon Nov 2 19:22:01 2026 -0500

    Document Laya setup and GPU use for users and the Hermes agent
    
    - plugins/metaskill-laya/skills/setup/SKILL.md: setup, model download, GPU
      support per platform, settings, and what each laya warning in a
      generalize result means. The add-on registers it as metaskill-laya:setup,
      so the agent sees it in its skills index and loads it on demand. It asks
      the user before any download or settings change and never runs pip or uv
      against Hermes's environment.
    - GPU: works on Linux with NVIDIA (PyPI's PyTorch includes CUDA) via
      `hermes config set METASKILL_LAYA_DEVICE gpu`. On Windows and with AMD
      GPUs the PyTorch Hermes installs is CPU-only and Hermes manages its
      environment, so the docs say the CPU is used.
    - `hermes metaskill-laya status` explains a gpu setting with no usable GPU
      and shows how to turn the GPU on.
    - README and after-install.md point to the skill and use hermes config set.

[33mcommit ae6aa2f154d2c0fa3e114ee3ca92da469b048e7a[m
Author: Manan <manan.kakkar.2005@outlook.com>
Date:   Mon Nov 2 19:13:51 2026 -0500

    Make Laya a self-contained optional add-on and run it after the regex layer
    
    metaskill's generalize now works on its own with the regex layer from main.
    The metaskill-laya plugin is an optional extra: when it is enabled, generalize
    also runs the Laya model, and nothing in core depends on it.
    
    Core (metaskill):
    - generalize_skill (pipeline.py) inventories the skill, runs the regex
      layer, then any extra detectors, and returns counts only. A failing or
      slow extra never fails the call: its error hint or a time-limit warning
      is reported and the regex results are kept.
    - extras.py: a registry the add-on fills when Hermes loads it. The plugins
      cannot import each other, so they share it through sys.modules.
    - detect_file_with_claims returns the spans the regex layer reported or
      rejected as example values; extras skip them.
    - New category personal_data, always left for review.
    - schemas.py and tools.py are back to main: no use_laya or
      laya_model_path, since installing the add-on is the opt-in.
    
    Add-on (plugins/metaskill-laya):
    - Holds all Laya code: detector, vendored pii_demo.py with its Apache-2.0
      LICENSE and NOTICE, and `hermes metaskill-laya status|download`.
    - Model pinned to revision 73d5a08 and checked against its SHA-256.
    - laya pinned to the tested range >=0.4.17,<0.5.
    - METASKILL_LAYA_MODEL loads a local copy of the model.
    - Drops hits that cannot be right whatever the label: a card number with
      letters, a 555 phone number, a one-character postal code.
    - after-install.md tells the user to restart and fetch the model.
    
    On the labeled benchmark, regex + Laya finds all 34 values with 2 false
    alarms (was 6) in 34 s on CPU (was 45 s) and 2.4 s on GPU.

[33mcommit e9e42177bb0bad30205884899d9e77b0749b49fc[m
Author: Manan <manan.kakkar.2005@outlook.com>
Date:   Mon Nov 2 18:55:00 2026 -0500

    Add GPU mode, skip nested candidates, and a regex vs Laya benchmark
    
    - METASKILL_LAYA_DEVICE=gpu uses an NVIDIA (CUDA) or AMD (ROCm) GPU when
      PyTorch sees one and falls back to CPU.
    - Skip candidates that are only pieces of a larger email, URL, card or
      phone candidate (about 20% less model work, same inputs).
    - benchmarks/pii_compare: a small labeled corpus and scripts that score
      the regex detector and Laya side by side.
    - examples/laya_demo.py: run the detector on a file and print what it finds.

[33mcommit 105a41ed512ba8f9f5dcc4e882d30f1101275016[m
Merge: 49b21c9 32a487e
Author: Manan <manan.kakkar.2005@outlook.com>
Date:   Mon Nov 2 18:54:52 2026 -0500

    Merge remote-tracking branch 'origin/main' into laya-pii-companion-plugin
    
    # Conflicts:
    #       metaskill/generalize/__init__.py

[33mcommit 32a487e14d874a0ea433a305cd6693d3be40ef99[m
Author: arora-jayant <81382724+arora-jayant@users.noreply.github.com>
Date:   Thu Oct 29 22:00:59 2026 -0400

    [MET-2] - Complete deterministic PII/config detection with phone and organization-host rules, false-positive tuning, and a full fixture test suite (#7)
    
    Co-authored-by: Cursor <cursoragent@cursor.com>

[33mcommit 49b21c99b159dd04973272ccbcbbf6233e02190b[m
Author: kakkarm2 <manan.kakkar.2005@outlook.com>
Date:   Thu Oct 22 02:36:52 2026 -0400

    Isolate Laya code in a subpackage and keep it clear of other generalize work
    
    Moves everything Laya-specific into metaskill/generalize/laya_pii/ (detector, vendored model code, and a skill walker that is a stopgap until the inventory stage is merged). generalize/__init__.py is now a thin entry point, and the result reports detectors=[laya].
    
    Tests move to tests/laya_detector/ with their own conftest, so tests/conftest.py and pytest.ini are untouched. Slow real-model tests are gated by METASKILL_RUN_SLOW=1 instead of a --runslow option, which only a root conftest can define.

[33mcommit ce7fa22163f06ccf9873f071c10e6dececa9afda[m
Author: kakkarm2 <manan.kakkar.2005@outlook.com>
Date:   Thu Oct 22 02:33:37 2026 -0400

    Run the Laya model on CPU by default
    
    On an Apple-silicon Mac laya picks the GPU (MPS) on its own and hung indefinitely in the Metal queue. load_detector now passes device=cpu unless METASKILL_LAYA_DEVICE is set.

[33mcommit c501abaacd6fc0bdbc32ed4224437607fd3ac4f3[m
Author: kakkarm2 <manan.kakkar.2005@outlook.com>
Date:   Thu Oct 22 01:45:12 2026 -0400

    Use a relative import so generalize loads when installed as a plugin
    
    Hermes loads the plugin as a package and does not put the repo root on sys.path, so the top-level 'from metaskill.generalize import laya_pii' failed and tools reported 'generalize is not implemented yet'. Adds a subprocess regression test that loads the repo as a package from another directory.

[33mcommit 3fadc412e17c1e6f180ef7924436404ab1fd9584[m
Author: kakkarm2 <manan.kakkar.2005@outlook.com>
Date:   Thu Oct 22 01:42:06 2026 -0400

    Tell the agent to ask the user before suggesting the Laya install
    
    When use_laya is set and laya is missing, generalize now returns laya_available=false and a next_step that tells the model to ask the user first and then point them to hermes plugins install metaskill-laya. The model never installs it on its own. The README now says the bare-name install needs catalog listing.

[33mcommit e13ffc161a9f1a70777aa660628fba235589cea7[m
Author: kakkarm2 <manan.kakkar.2005@outlook.com>
Date:   Thu Oct 22 01:37:46 2026 -0400

    Add opt-in Laya PII detection and metaskill-laya companion plugin
    
    generalize can detect personal data with the local Laya Experts model when called with use_laya=true. Detection is Laya-only, with no regex fallback: a missing laya install returns an error pointing to the companion plugin.
    
    The model is cached per path instead of reloading on every call. laya is supplied by the optional metaskill-laya plugin (plugins/metaskill-laya), which declares it as a dependency so Hermes asks for consent at install time.
    
    Adds tests/conftest.py with the --runslow option and a fake detector so the fast tests need no model or network.

[33mcommit 994d77df9b14be402fb65f68c78142f162434b57[m
Author: Ryan Lin <119691704+PrimitiveTechExperience@users.noreply.github.com>
Date:   Wed Oct 21 18:02:20 2026 -0400

    Met 19 evaluation implement judge py (#10)
    
    * judge.py skeleton
    
    * Hermes integration - accidental other ai
    
    * Unified interface - might change later down the line
    
    * Some more docs
    
    * judge.py skeleton
    
    * Hermes integration - accidental other ai
    
    * Unified interface - might change later down the line
    
    * Some more docs
    
    * Uniting the conflicting judge implementation in this file and safety
    
    * test issue fix
    
    * Forgot to update some of the docs
    
    * Bug and security fixes
    
    * DeterministicJudge, make_judge now unavailable until runner sandbox implementation
    
    * Leaky symlinks, model output fixes
    
    * Some more bug fixes

[33mcommit f1541da950dfdbd3ff4feaeb81a961c4dab40845[m
Author: Julius Henriques <144366964+Julius14h@users.noreply.github.com>
Date:   Tue Oct 20 22:15:41 2026 -0400

    Safety evaluation: static tier (Cisco + own checks) and local warnings (#8)
    
    * Make metaskill and metaskill.evaluate importable packages
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Add safety findings: backend interface, merging and scoring
    
    findings.py implements section 5 of evaluate-safety.md: BackendStatus,
    BackendResult, the SafetyBackend protocol, merging duplicate findings
    across backends, and the worst-finding safety score. Brings over main's
    pytest.ini so pytest does not import the plugin's root __init__.py.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Ask the user before installing Cisco when it is missing
    
    Record the decision from the PR #2 review: the agent asks whether to
    install the pinned scanner before evaluating. If the user declines,
    safety is partial with an install hint and the tool output warns.
    Making Cisco required is a new open question.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * initial design docs for safety implementation
    
    * Pass the loaded Skill to safety backends instead of a directory
    
    Own checks and the LLM review need file contents, and the runner already
    loads each skill with Skill.load(), so backends no longer re-read files.
    Cisco uses skill.path. Updates the protocol, the safety design and the
    findings plan.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Add Cisco skill-scanner backend for the Safety dimension
    
    cisco.py runs the scanner on one skill and converts its output into a
    BackendResult. Behaviour verified against 2.2.1:
    - LLM setup failures (AnalyzerConfigurationError) rerun as static.
    - LLM run-time failures do not raise; they are read from
      analyzers_failed and reported as llm-unavailable.
    - The scanner's diagnostic notices go into detail, not findings, so a
      large skill or a down API does not lower the score.
    - hardcoded_secrets and policy_violation are routed to Configuration
      Cleanliness as evidence.
    
    Tests run against scanner output recorded from real Hermes skills, so
    CI does not need the scanner; one real-scanner test skips without it.
    Docs record decisions 2 and 3 and an open item on letting the deep
    review downgrade in-scope network findings.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Add safety.score(): run backends, merge findings, set status
    
    score() runs each backend for the tier, merges findings from those that
    produced usable results, and turns backend statuses into the dimension
    status. When no backend could run the value is None, never 1.0.
    Also adds missing_dependencies() for the install prompt and versions()
    for the report header. Until checks.py and review.py exist, the
    default backend list is Cisco only.
    
    Cisco's setup-failure message is now passed through unchanged.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Settle own-check design: scanning, severities, symlinks, local warnings
    
    - Own checks scan everything; prose matches that the same sentence warns
      against are skipped and listed in detail, and re-checked at deep.
    - Deep review re-check can only restore skipped matches, with
      restrictions on the call; open item 7 now covers any lenient change.
    - Severities decided; recursive-delete severity set by its target, with
      plain rebuildable folders at MEDIUM and system or home at CRITICAL.
    - Symlink rules for links shipped in or created by the skill.
    - New local.py plan: unscored warnings when a delete target on this
      machine looks important.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Add own deterministic safety checks and run them in score()
    
    checks.py implements docs/safety/checks.md: rules for
    destructive-unconfirmed, credential-logging and untrusted-source, plus
    symlinks shipped in or created by the skill. Prose is scanned too;
    matches the same sentence warns against are skipped and listed in
    detail. Unconfirmed deletes are judged by their target, from rebuildable
    folders (MEDIUM) to system or home folders and possibly-empty variables
    (CRITICAL). delete_targets() exposes them for the planned local warnings.
    
    score() now runs Cisco and own checks, and versions() reports
    checks_version. Tested on six fixture skills and run against the 59
    local Hermes skills, which led to narrowing three noisy rules; results
    are recorded in checks.md.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Add local warnings for deletes that look important on this machine
    
    local.py looks up each unconfirmed delete target from the own checks on
    the machine running the evaluation and warns when it is a home or
    system folder, a git repository, a link the delete would follow, or a
    folder with many or recently changed files. Metadata only, bounded, and
    never scored. models.py gains LocalWarning and SkillResult.local_warnings,
    and evaluate.md section 2.1 documents the field.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Bring the safety design doc in line with what was built
    
    Replace the Cisco sketch and its two [VERIFY] items with the verified
    behaviour and a link to docs/safety/cisco.md, correct the LLM failure
    rule in section 4, and update the module layout, testing and limitations
    sections, including the deep tier's context budget and known false
    positives.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Route Cisco's LLM analyzer through Hermes's ctx.llm, with no API key
    
    Hermes never gives plugins keys, so Cisco's LLM analyzer cannot be given
    one. hermes_bridge.py registers a LiteLLM custom provider that forwards
    each of Cisco's requests to ctx.llm; Cisco only gets a placeholder key
    and nothing is read from the environment.
    
    ctx.llm cannot ask providers to enforce a schema (it sends strict=False)
    and fails the call on a strict mismatch, so the schema is sent as
    guidance in JSON mode and Cisco's own parser checks the reply. A reply
    that does not fit is asked for once more, naming the problems.
    
    Live test through Hermes on gpt-5.4-nano, 5 deep scans of 5 skills: the
    LLM layer failed 10/25 with the schema enforced by ctx.llm, 7/25 with it
    as guidance, 2/25 with the retry. Safety scores were identical across
    all successful runs.
    
    score() and default_backends() now take ctx.llm instead of judge
    provider and model settings.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Document how Cisco reaches the model through ctx.llm
    
    cisco.md gains a section on the adapter, the strict-schema gap in
    ctx.llm, the retry, and the live measurement (40% -> 28% -> 8% LLM
    failures over 25 scans, identical scores across successful runs). The
    safety design drops the key and same-model-as-judge wording, records the
    measured per-scan cost, and adds the variation and failure rate to the
    limitations.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Recognise Windows paths and commands in own checks and local warnings
    
    Ryan's review: local warnings only understood /-rooted paths, so Windows
    users got none, and Windows system folders were not judged CRITICAL.
    
    Own checks now convert Windows targets (C:\..., %USERPROFILE%,
    $env:VAR, %TEMP%) to the forward-slash form the existing rules use,
    match rmdir /s, rd /s and del /s, scan .bat/.cmd/.psm1 scripts, follow
    cmd and PowerShell variable assignments, count set /p, choice and
    -Confirm as confirmations, and split backslash arguments without shell
    escaping. Local warnings resolve the same Windows forms, matching
    environment variable names in any case. Tested on Linux only.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Fix doc nits from the PR #8 review and align review.md with judge.py
    
    - evaluate.md: the per-skill fields are four lists, not three.
    - review.md: count the warning re-check and possible retries in the
      number of calls; replace the assumed judge.py interface with Ryan's
      real Judge.review(), which already does the category, severity and
      quote checks; note that judge.py's strict json_schema will likely hit
      the failures measured with Cisco, and that the Cisco adapter should
      use judge.py's metaskill_judge model setting once it merges.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    ---------
    
    Co-authored-by: Claude Opus 5.5 <noreply@anthropic.com>

[33mcommit 0d225e4693dcebf7bc6df39f838178de13392755[m
Author: Manan Kakkar <94943688+MananKakkar1@users.noreply.github.com>
Date:   Fri Oct 16 23:52:53 2026 -0400

    Add metaskill tool schema and operation routing (MET-14) (#5)
    
    Tool takes an operation enum (generalize|personalize) with skill_path,
    config_path and output_path, and routes lazily to the pipelines. The
    /metaskill command forwards the request to the agent via inject_message so
    the model resolves skill names to paths. Handlers always return JSON and
    never raise.

[33mcommit e719878a762752601fd2c2299c956d408d2ad330[m
Author: Ryan Lin <119691704+PrimitiveTechExperience@users.noreply.github.com>
Date:   Thu Oct 15 14:38:52 2026 -0400

    Add evaluation data models (#4)
    
    * boilerplate models.py
    
    * Some more models
    
    * Align models.py with the reviewed evaluation docs (PR #2)
    
    - Finding gains message; ReportMeta gains checks_version, also passed
      through EvaluationConfig.to_meta.
    - Add Status and DimensionStatus. DimensionScore carries status and
      detail, and SkillResult keeps a status per dimension.
    - Skipped or errored dimensions score None; Scores accepts None.
    - Remove ReportMeta.is_comparable, since every ranking is fresh.
    - Default output_dir to evaluation-reports/.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    ---------
    
    Co-authored-by: Julius14h <juliusjhenriques@gmail.com>
    Co-authored-by: Claude Opus 5.5 <noreply@anthropic.com>

[33mcommit 37fda2c861dfd0f30c24758ab44a0381f9c274ff[m
Author: Ryan Lin <119691704+PrimitiveTechExperience@users.noreply.github.com>
Date:   Thu Oct 15 14:27:23 2026 -0400

    Evaluate design docs (#2)
    
    * Draft of design doc
    
    * draft complete - need some more info
    
    * Add design doc for evaluate safety
    
    * Add hermes testing results for cisco scanner
    
    * add full set of cisco scanner checks
    
    * Add cisco adapter implementation
    
    * Record settled decisions in safety design doc
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Align evaluate.md with the safety design doc
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Added some more details + finalizations to the evaluation docs - still some open questions remaining
    
    * oops needed to add stuff for clarity
    
    * inconsistencies fixes
    
    * Rename Finding fields in safety doc to match evaluate.md
    
    quoted_text -> quote and rule_id -> rule, so the safety design and the
    report format in evaluate.md section 2.1 use the same field names.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Align Finding and report header across the evaluation docs
    
    - Add dimension to the safety doc's Finding and message to the report's
      findings table, so both docs list the same fields.
    - Drop source_version from Finding. Backend versions are recorded once in
      the report header: scanner_version for Cisco, and a new checks_version
      for our own rule set, which the ranking rule now also compares.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Add per-dimension status to the report and tidy evaluate.md
    
    - Report records a status, reason and detail per dimension, so a safety
      scan that fell back to static is visible and ranked as static.
    - Resolve the open question on AI-written tasks (settled by 4.3) and add
      open questions on running skills with CRITICAL findings and on scoring
      skipped dimensions.
    - Make the example's safety score match its HIGH finding.
    - Fix typos.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Settle where reports are stored and make every ranking fresh
    
    - Each call writes evaluation-reports/[timestamp]/ with the ranking and
      a logs folder per skill. Reports are kept, never deleted.
    - rank never reads earlier reports or reuses results, so the skills in
      one ranking always share settings. A deep scan that fell back to
      static is flagged rather than refused.
    - Close the persistence and full-log open questions, and the safety
      doc's caching question.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Ask which skills to rank, and score skipped dimensions as null
    
    - When a request does not name skills, the agent asks the user: all
      installed skills or a listed set.
    - Skipped and errored dimensions score null. How overall and rank treat
      null stays open until implementation.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Mark baseline sharing as an open question in evaluate.md
    
    Rewrite the cost limitation to explain where the cost comes from and
    what sharing a baseline would save, and list it in the open questions.
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Update the config header description for fresh rankings
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    * Describe the timestamp header as the run folder name
    
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    
    ---------
    
    Co-authored-by: Julius14h <juliusjhenriques@gmail.com>
    Co-authored-by: Claude Opus 5.5 <noreply@anthropic.com>

[33mcommit bf4d720bd0a28b1858384f891045c34206af7003[m
Author: Manan Kakkar <94943688+MananKakkar1@users.noreply.github.com>
Date:   Thu Oct 15 14:10:37 2026 -0400

    MET-6: Finalize generalization/personalization design doc (#3)
    
    * Add generalization and personalization design
    
    * Update design.md to remove draft status and scope
    
    Removed status and scope details from the design document.
    
    * Refine design.md by removing open questions
    
    Removed open questions from output and handler sections in design documentation.
    
    * Updated design doc - detecting using Regex and LLM layers
    
    * Resolve open questions in generalization design doc (MET-6)
    
    Settle file allowlist and limits, JSON config, non-interactive review flow,
    classifier interface and privacy controls, personalize report, single tool
    with an operation enum, real-skill annotation format, and parser extraction
    scope. Replace the open questions section with a decisions table.
    
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    
    * Reword design doc to describe behavior instead of instructing the writer
    
    * Expand skill file detection and clarify environment variable handling
    
    ---------
    
    Co-authored-by: arora-jayant <jayant23@icloud.com>
    Co-authored-by: Claude Sonnet 5.5 <noreply@anthropic.com>
    Co-authored-by: tatisoha <s.tati@mail.utoronto.ca>

[33mcommit ce3053492017cf4f7e546b7b88fb4be7ee2d2b8f[m
Author: Manan <manan.kakkar.2005@outlook.com>
Date:   Thu Oct 8 19:16:56 2026 -0400

    github actions fix

[33mcommit 2fe443183bd8c8dbdfc5bbae7edfa90144150160[m[33m ([m[1;31morigin/feature/deterministic-detection[m[33m)[m
Author: Manan <manan.kakkar.2005@outlook.com>
Date:   Thu Oct 8 19:10:58 2026 -0400

    Adding dummy test

[33mcommit abc176248ed8c4e6b43e10f726a5f63e8520e4bb[m
Author: Manan <manan.kakkar.2005@outlook.com>
Date:   Sun Oct 4 13:06:04 2026 -0400

    Setup pytest github actions pipeline

[33mcommit 3b03c508257b8a757d00db0a6b1708cd81a2b4b1[m
Author: Manan Kakkar <manan.kakkar.2005@outlook.com>
Date:   Sat Oct 3 18:01:51 2026 -0400

    Add hermes plugin starter code and repo structure

[33mcommit c01731703727d9aaee6fdc4b4679e8d7f60ae581[m
Author: Manan Kakkar <manan.kakkar.2005@outlook.com>
Date:   Sat Oct 3 17:33:57 2026 -0400

    first commit
