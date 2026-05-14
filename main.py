import os
import streamlit as st
from groq import Groq
from dotenv import load_dotenv
from secure_renderer import (
    RenderError,
    SandboxUnavailableError,
    ValidationError,
    render_scene_in_sandbox,
    strip_markdown_fences,
)

# Load environment variables
load_dotenv()

# Configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MODEL_NAME = "openai/gpt-oss-120b"

# Initialize GROQ client
client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

st.set_page_config(page_title="AI Video Generator", layout="wide")
st.title("🎥 AI-driven Educational Video Generator")

st.markdown(
    "Provide a prompt describing the animation or visualization you want. "
    "The AI will generate Manim code, compile it, and produce an MP4 video."
)
st.info(
    "Generated scenes are validated and rendered inside a local Docker/Podman "
    "sandbox with networking disabled. Install Docker Desktop or Podman before "
    "using Generate Video."
)

# User prompt
title = st.text_input("Video Title (for filename)")
prompt = st.text_area(
    "Describe your scene, e.g. 'Show a browser on left, server in middle, database on right, with arrows.'",
    height=150
)
show_code = st.checkbox("Show generated Manim code")

if st.button("Generate Video"):
    if not prompt.strip():
        st.error("Please enter a prompt.")
    elif client is None:
        st.error("Set the GROQ_API_KEY environment variable before generating videos.")
    else:
        with st.spinner("Generating Manim script from LLM..."):
            # Use chat completions with explicit messages
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": 
                        "You are an expert at writing safe Python scripts using the "
                        "Manim animation library."
                    },
                    {"role": "user", "content": 
                        "Generate only a complete, self-contained Manim script as Python "
                        "code in a single .py file, with no markdown fences or extra text "
                        "before or after. The script must define exactly one scene class, "
                        "avoid top-level side effects, use only Manim plus optional math "
                        "or numpy imports, and never access the filesystem, network, "
                        f"environment variables, subprocesses, or OS APIs. Scene prompt: {prompt}"
                    }
                ],
                max_tokens=2048,
                temperature=0.3
            )
            code = response.choices[0].message.content.strip()
            code = strip_markdown_fences(code)

        if show_code:
            st.subheader("Generated Manim Code")
            st.code(code, language="python")

        with st.spinner("Rendering video inside the sandbox..."):
            try:
                result = render_scene_in_sandbox(code, title)
                st.success("Video generated successfully inside the sandbox!")
                st.video(str(result.video_path))
            except ValidationError as exc:
                st.error(f"Generated code was rejected by the safety validator: {exc}")
            except SandboxUnavailableError as exc:
                st.error(str(exc))
            except RenderError as exc:
                st.error(str(exc))

# Footer
st.markdown("---")
st.caption("Powered by Manim, Streamlit, and Groq LLM")
