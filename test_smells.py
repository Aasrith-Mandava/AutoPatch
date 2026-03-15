def authenticate_user():
    # SonarQube Rule python:S2068 - Hardcoded credentials
    password = "SuperSecretPassword123!"
    
    # SonarQube Rule python:S1481 - Unused local variable
    unused_var = 42

    print("Authenticating...")
    return password == "admin"
