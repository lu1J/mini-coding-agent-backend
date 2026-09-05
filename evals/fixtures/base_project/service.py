from models import User


def create_user(name: str) -> User:
    return User(name=name)
