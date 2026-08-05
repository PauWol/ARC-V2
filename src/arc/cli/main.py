import cyclopts

from arc.boot.boot import main as boot

app = cyclopts.App()


@app.default
async def start() -> None:
    await boot()


if __name__ == "__main__":
    app()
