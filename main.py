from llama_cpp import Llama
from llama_cpp.llama_types import ChatCompletionRequestMessage


MODEL_PATH = "/home/paul/arc/models/unsloth__Qwen3.5-4B-GGUF/Qwen3.5-4B-Q4_K_M.gguf"


llm = Llama(
    model_path=MODEL_PATH,
    n_ctx=16384,
    n_gpu_layers=-1,
    n_batch=512,
    verbose=False,
)

SYSTEM_PROMPT = """
You are an expert frontend developer and UI/UX designer.

Create a complete, polished, modern website based exactly on the user's request.

Rules:
- Return ONLY raw HTML.
- Start with <!DOCTYPE html> and end with </html>.
- Put CSS in <style> and JavaScript in <script>.
- No Markdown code fences.
- No explanations or questions.
- Make it responsive and visually impressive.
- Implement all requested features and interactions.
- Use realistic content and data.
- Do not use external dependencies.
- Do not use base64, data URLs, or huge SVGs.
- Do not replace the requested website with a generic template.
"""


def generate_website(specification: str) -> str:

    messages: list[ChatCompletionRequestMessage] = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": (
                "Generate the complete website described below.\n\n"
                "WEBSITE SPECIFICATION:\n"
                f"{specification}"
            ),
        },
    ]

    response = llm.create_chat_completion(
        messages=messages,
        temperature=0.2,
        top_p=0.9,
        max_tokens=8000,
        repeat_penalty=1.1,
        stream=True,
    )

    html = ""

    for chunk in response:
        token = chunk["choices"][0]["delta"].get("content", "")

        if token:
            print(token, end="", flush=True)
            html += token

    return html


print("Complex HTML Generator")
print("Type 'exit' to quit.")

while True:
    specification = input("\nDescribe the website:\n> ")

    if specification.lower() in {"exit", "quit"}:
        break

    print("\nGenerating website...\n")

    html = generate_website(specification)

    print("\n\nGeneration complete.")
