def process_data(data1, data2, data3, data4, data5, data6, data7, data8):
    # SonarQube Rule python:S107 - Functions should not have too many parameters
    pass

def generic_exception_handling():
    # SonarQube Rule python:S112 - Generic exceptions should not be raised
    try:
        x = 1 / 0
    except Exception as e:
        raise Exception("An error occurred")

def duplicate_strings_example():
    # SonarQube Rule python:S1192 - String literals should not be duplicated
    print("This is a duplicated string that appears multiple times.")
    print("This is a duplicated string that appears multiple times.")
    print("This is a duplicated string that appears multiple times.")
    print("This is a duplicated string that appears multiple times.")

def perform_calculation():
    # SonarQube Rule python:S125 - Sections of code should not be commented out
    # print("Debugging calculation...")
    # a = 10
    # b = 20
    # result = a + b
    # return result
    
    return 42

class EmptyClassExample:
    # SonarQube Rule python:S2094 - Classes should not be empty
    pass
