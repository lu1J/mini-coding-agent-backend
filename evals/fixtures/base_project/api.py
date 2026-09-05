from service import create_user


def create_user_api(name: str):
    user = create_user(name)
    return {
        "name": user.name,
    }
