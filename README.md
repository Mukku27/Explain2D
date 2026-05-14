# Explain2D — AI-driven Educational Video Generator

Turn a one-line prompt into an animated MP4. Explain2D is a Streamlit app that
asks a Groq-hosted LLM to write a Manim scene, validates the generated Python
against a strict allowlist, and renders it inside a hardened Docker/Podman
sandbox — so model output never executes on your host.

> Example prompts: *"Visualize bubble sort on 10 random numbers"*, *"Show
> Earth's orbit around the Sun"*, *"Animate forward propagation in a 3-4-2
> neural network"*. Pre-rendered samples live in [`generated_videos/`](generated_videos).

---

## Features

- **Prompt-to-video pipeline** — Describe what you want; get back an MP4.
- **LLM code generation** — Uses Groq's `openai/gpt-oss-120b` model to author a
  self-contained Manim script.
- **Static safety validator** — An AST walker rejects unsafe imports,
  filesystem/network/OS access, dunder tricks, dynamic execution, decorators,
  top-level side effects, and more *before* any code runs.
- **Container-isolated rendering** — Rendering happens inside Manim's official
  container image with `--network none`, a read-only root filesystem, dropped
  Linux capabilities, no-new-privileges, CPU/memory/PID caps, and a timeout.
- **Fail-closed design** — If Docker/Podman isn't installed or running, the app
  refuses to render rather than falling back to the host.
- **Streamlit UI** — Minimal interface; toggle "Show generated Manim code" to
  inspect what the model produced.
- **Reusable output** — Final MP4 is copied to `generated_videos/<title>.mp4`.

---

## Architecture

```text
┌─────────────────┐    prompt    ┌────────────────────┐    Manim code
│ Streamlit UI    │ ───────────▶ │ Groq LLM           │ ───────────────┐
│ (main.py)       │              │ openai/gpt-oss-120b│                │
└─────────────────┘              └────────────────────┘                │
        ▲                                                              ▼
        │                                              ┌─────────────────────────┐
        │       MP4 path                               │ AST validator           │
        │ ◀─────────────────────────────────────────── │ (secure_renderer.py)    │
        │                                              └─────────────────────────┘
        │                                                              │
        │                                                              ▼
        │                                              ┌─────────────────────────┐
        │                                              │ Docker / Podman sandbox │
        │                                              │  --network none         │
        │                                              │  --read-only            │
        │                                              │  --cap-drop ALL         │
        │                                              │  CPU/mem/PID/timeout    │
        │                                              │  manim render scene     │
        │                                              └─────────────────────────┘
        │                                                              │
        └──────────────────────────────────────────────────────────────┘
                       generated_videos/<title>.mp4
```

---

## Project layout

```text
Explain2D/
├── main.py                       # Streamlit entry point (UI + LLM call)
├── secure_renderer.py            # AST validator + Docker/Podman sandbox runner
├── requirements.txt              # Host Python dependencies (no Manim on host)
├── .env.example                  # Template for environment variables
├── tests/
│   └── test_secure_renderer.py   # unittest suite for validator + sandbox CLI
├── generated_videos/             # Final MP4 outputs (one per render)
├── media/                        # Manim intermediate artifacts (svg/partials)
└── LICENSE                       # MIT
```

---

## Requirements

- **Python 3.10+** (`secure_renderer.py` uses PEP 604 union syntax).
- **A Groq API key** — sign up at <https://console.groq.com/>.
- **Docker Desktop** or **Podman** running locally. The renderer fails closed
  if neither is available.

> Manim itself is **not** installed on the host — it runs inside the
> `manimcommunity/manim` container, which avoids the brittle FFmpeg/Cairo/Pango
> system-package dance and keeps generated code off your machine.

---

## Installation

### 1. Clone

```bash
git clone https://github.com/mukku27/Explain2D.git
cd Explain2D
```

### 2. Create a virtualenv and install host dependencies

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` only pulls Streamlit, the Groq SDK, and python-dotenv — the
heavy media stack lives inside the container.

### 3. Install a container runtime

- **Docker Desktop**: <https://www.docker.com/products/docker-desktop/>
- **Podman**: <https://podman.io/>

Confirm it's running:

```bash
docker info        # or: podman info
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and set at minimum:

```dotenv
GROQ_API_KEY=your_groq_api_key_here
```

Optional knobs (defaults shown):

| Variable                | Default                          | Purpose                                            |
| ----------------------- | -------------------------------- | -------------------------------------------------- |
| `MANIM_SANDBOX_IMAGE`   | `manimcommunity/manim:v0.17.3`   | Container image used for rendering.                |
| `MANIM_SANDBOX_TIMEOUT` | `120`                            | Hard timeout (seconds) for one render.             |
| `DOCKER_HOST`           | —                                | Forwarded to the Docker/Podman CLI when set.       |
| `DOCKER_CONTEXT`        | —                                | Forwarded to the Docker/Podman CLI when set.       |
| `DOCKER_TLS_VERIFY`     | —                                | Forwarded to the Docker/Podman CLI when set.       |
| `DOCKER_CERT_PATH`      | —                                | Forwarded to the Docker/Podman CLI when set.       |

Only a small, explicit allowlist of environment variables
(`SAFE_DOCKER_CLIENT_ENV_KEYS` in `secure_renderer.py`) is forwarded to the
container CLI. The model never sees your shell environment.

### 5. (Recommended) Pre-pull the sandbox image

This avoids a long first-run delay:

```bash
docker pull manimcommunity/manim:v0.17.3
```

---

## Usage

Start the app:

```bash
streamlit run main.py
```

Then in the browser:

1. Enter a **Video Title** — used as the output filename (sanitized to
   `A-Z`, `a-z`, `0-9`, `_`, `-`, `.`).
2. Write a **prompt** describing the scene.
3. *(Optional)* Tick **Show generated Manim code** to inspect what the LLM
   wrote.
4. Click **Generate Video**.

The app will:

1. Ask Groq to generate a single-class Manim script (max 2048 tokens, temp 0.3).
2. Strip any stray Markdown fences.
3. Run the static safety validator.
4. Spawn a sandboxed `manim -ql` render.
5. Display the resulting MP4 inline and write it to
   `generated_videos/manim_script_<title>.mp4`.

---

## Security model

The threat model assumes the LLM may emit malicious or buggy Python. Defenses
are layered so any single bypass is not enough:

### Layer 1 — Static AST validation (`validate_generated_code`)

Generated code is parsed and walked before it ever touches the container.
The validator enforces an **allowlist** rather than a blocklist:

- **Imports** — only `manim` (wildcard allowed), `math`, and `numpy` (as `np`)
  are accepted. Anything else is rejected.
- **Forbidden module names** — references to low-level / IO / serialization /
  introspection modules are denied. The authoritative list is
  `FORBIDDEN_MODULE_NAMES` in
  [`secure_renderer.py`](secure_renderer.py).
- **Forbidden builtins** — `eval`, `exec`, `compile`, `open`, `__import__`,
  `getattr`, `setattr`, `delattr`, `globals`, `locals`, `vars`, `breakpoint`,
  `input`, `help`. See `FORBIDDEN_CALL_NAMES`.
- **Dangerous constructs** — `try/except`, `raise`, `with`, `del`, `global`,
  `nonlocal`, `lambda`, `yield`/`yield from`, `async`/`await`, and all
  decorators. See `FORBIDDEN_NODE_TYPES`.
- **Dunder access** — any `__name__`-style attribute, import, assignment
  target, or method definition is rejected.
- **Top-level side effects** — only imports, class/function definitions,
  docstrings, and simple `config.*` assignments are allowed at module scope.
  Function calls at module scope are rejected.
- **Scene-shape check** — must define **exactly one** subclass of a known
  Manim scene base (`Scene`, `ThreeDScene`, `MovingCameraScene`,
  `ZoomedScene`, `VectorScene`, `LinearTransformationScene`).

### Layer 2 — Container sandbox (`render_scene_in_sandbox`)

If validation passes, rendering runs in a one-shot container with:

- `--network none` — no outbound or inbound networking.
- `--read-only` root filesystem + size-capped `/tmp` tmpfs (`noexec,nosuid`).
- `--cap-drop ALL` and `--security-opt no-new-privileges`.
- `--memory 768m`, `--cpus 1.0`, `--pids-limit 256`.
- `--user $UID:$GID` on POSIX hosts (non-root inside the container).
- A scrubbed environment (`HOME=/tmp`, `PYTHONDONTWRITEBYTECODE=1`) — your
  `GROQ_API_KEY` and other host secrets are **never** passed in.
- A hard wall-clock timeout (default 120 s).
- A bind-mounted tempdir that is deleted after the render.

### Layer 3 — Fail closed

If Docker/Podman is missing or the daemon isn't running, the app raises
`SandboxUnavailableError` and refuses to execute the generated code. There is
no host-fallback path.

---

## Running the tests

The suite covers the validator's accept/reject behaviour and the construction
of the sandbox command:

```bash
python -m unittest discover tests
```

Expected: `OK` with 6 tests passing. The tests don't spawn a real container,
so Docker/Podman is not required to run them.

---

## Example prompts

| Domain                | Prompt                                                                                                  |
| --------------------- | ------------------------------------------------------------------------------------------------------- |
| Networking            | "Browser on the left connects to a server in the middle, which queries a database on the right."        |
| Algorithms            | "Bubble sort on 10 random numbers with comparison highlights."                                          |
| Physics               | "Earth's orbit around the Sun with arrows for velocity and gravitational force."                        |
| Math                  | "Pythagorean theorem with animated squares on each side of a right triangle."                           |
| Linear algebra        | "Transform the 2D basis vectors under the matrix [[2,1],[1,3]]."                                        |
| Machine learning      | "Forward propagation in a 3-4-2 neural network with weighted edges lighting up."                        |

See `generated_videos/` for pre-rendered examples corresponding to several of
these.

---

## Troubleshooting

- **"Install Docker Desktop or Podman to render generated scenes safely."**
  Neither `docker` nor `podman` was found on `PATH`. Install one and re-run.

- **"Docker is installed but not ready."**
  The CLI is present but `docker info` failed. Start Docker Desktop (or the
  Podman machine) and try again.

- **"Set the `GROQ_API_KEY` environment variable before generating videos."**
  Your `.env` is missing or empty. Confirm `cp .env.example .env` and fill in
  the key, then restart Streamlit so `python-dotenv` reloads it.

- **"Generated code was rejected by the safety validator: …"**
  The model wrote something the AST validator doesn't allow (e.g. an
  out-of-allowlist import). Click **Generate Video** again — the model is
  non-deterministic enough that retries usually succeed. Tightening the prompt
  also helps.

- **"Sandboxed rendering timed out after 120 seconds."**
  Complex scenes may need more time. Raise `MANIM_SANDBOX_TIMEOUT` in `.env`.

- **First render is very slow.**
  Docker is pulling `manimcommunity/manim:v0.17.3` (~1 GB). Pre-pull it with
  `docker pull manimcommunity/manim:v0.17.3`.

---

## Contributing

Pull requests are welcome. If you're modifying the safety surface
(`secure_renderer.py`), please add or update tests in
`tests/test_secure_renderer.py` and run `python -m unittest discover tests`
before opening a PR.

---

## License

MIT — see [`LICENSE`](LICENSE).

---

## Acknowledgements

- [Manim Community](https://www.manim.community/) for the animation engine and
  container image.
- [Groq](https://groq.com/) for fast LLM inference.
- [Streamlit](https://streamlit.io/) for the UI layer.
