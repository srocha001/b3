from app.services.auth import hash_password, verify_password


def test_password_hash_is_not_the_plaintext():
    password = "senha-local-forte"
    password_hash = hash_password(password)

    assert password_hash != password
    assert verify_password(password, password_hash)
    assert not verify_password("senha-errada", password_hash)
