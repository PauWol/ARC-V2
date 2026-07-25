from arc.foundation.constants import load_dot_env
from arc.foundation.logger import setup_logging


def main():

    env_loaded = load_dot_env()

    logger = setup_logging()

    if not env_loaded:
        logger.warning("ARC .env not found. Using default configuration.")

    # TODO: Add Pulse startup

    logger.info("Boot startup completed!")


if __name__ == "__main__":
    main()
