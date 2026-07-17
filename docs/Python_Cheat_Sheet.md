# Python Cheat Sheet

This document contains Python syntax, examples, and programming concepts used throughout the BTS project.

It will grow as the project grows.

# Python Packages

## What is a package?

A package is a folder that groups related Python modules together.

Packages help organize large projects into logical components.

Example:

```
app/
    communications/
    execution/
    risk/
```

Each package is responsible for one part of the application.

---

## __init__.py

The file:

```
__init__.py
```

marks a directory as a Python package.

It can also perform package initialization, but in many projects it begins as
an empty file.

Example:

```
communications/
    __init__.py
    sender.py
    receiver.py
```


# Python Modules vs Packages

## Module

A module is a single Python file.

Example:

```
sender.py
```

A module usually has one primary responsibility.

---

## Package

A package is a folder containing related Python modules.

Example:

```
communications/

    __init__.py

    sender.py

    receiver.py
```

Packages organize large applications into logical components.

---

## Design Goal

Each module should have one clearly defined responsibility.

Examples:

- sender.py → send messages
- receiver.py → receive messages
- validator.py → validate messages
- logger.py → write log files

This design makes software easier to understand, test, and maintain.

# Object-Oriented Programming Concepts

## Inheritance

When a class is defined with parent classes in parentheses:

```python
class Environment(str, Enum):
```

the parentheses **do NOT pass constructor arguments**.

They specify the **base classes** (parent classes) that the new class inherits from.

Think of it like Java:

```java
public class Environment extends Enum
```

except Python supports **multiple inheritance**.

In this example:

- `str` allows enum members to behave like normal strings.
- `Enum` provides all of Python's enumeration functionality.

---

## Creating an Object vs Defining a Class

### Defining a class

```python
class Car:
```

This creates the blueprint.

### Creating an object

```python
my_car = Car()
```

This creates an instance from the blueprint.

Remember:

- `class Car(...)` → inheritance
- `Car(...)` → constructor call

---

# Decorators (@)

A decorator modifies or enhances the class or function immediately below it.

Example:

```python
@dataclass
class TradeRequest:
```

Conceptually this is similar to:

```python
TradeRequest = dataclass(TradeRequest)
```

The decorator receives the class and returns an enhanced version.

Decorators always begin with:

```python
@
```

---

# @dataclass

`@dataclass` automatically creates common methods that almost every data object needs.

Without it we would have to manually write:

- `__init__()`
- `__repr__()`
- `__eq__()`

Example:

```python
@dataclass
class Person:
    name: str
    age: int
```

Python automatically creates the constructor.

---

## frozen=True

```python
@dataclass(frozen=True)
```

Creates an immutable object.

After construction:

```python
person.age = 50
```

will raise an error.

Useful when an object represents information that should never change.

---

## slots=True

```python
@dataclass(slots=True)
```

Restricts the object to only the declared attributes.

Benefits:

- Prevents accidental new attributes
- Helps catch spelling mistakes
- Reduces memory usage

---

# Instance Methods

Receive:

```python
self
```

Example:

```python
def print_name(self):
```

Called on an object:

```python
person.print_name()
```

---

# Class Methods

Receive:

```python
cls
```

Declared using:

```python
@classmethod
```

Example:

```python
@classmethod
def from_dict(cls, message):
```

Called on the class:

```python
TradeRequest.from_dict(message)
```

Class methods are commonly used as **alternative constructors**.

---

# Static Methods

Declared using:

```python
@staticmethod
```

Receive neither:

- self
- cls

Useful for helper functions that logically belong inside the class.

Example:

```python
@staticmethod
def add(a, b):
    return a + b
```

---

# isinstance()

Checks whether an object is a particular type.

Example:

```python
isinstance(5, int)
```

returns

```python
True
```

Example:

```python
isinstance("5", int)
```

returns

```python
False
```

General form:

```python
isinstance(object, type)
```

---

# not

Reverses a Boolean value.

```python
not True
```

returns

```python
False
```

```python
not False
```

returns

```python
True
```

Common example:

```python
if not isinstance(value, int):
```

means

> If value is NOT an integer.

---

# Boolean Expressions

Anything that evaluates to either:

- True
- False

Examples:

```python
x > 5
```

```python
isinstance(name, str)
```

```python
count == 10
```

These are often called:

- Boolean expressions
- Conditions
- Predicates

---

# try / except

Used when an operation might fail.

```python
try:
    value = int(text)
except ValueError:
    print("Invalid number")
```

General workflow:

Attempt operation

↓

If successful

↓

Continue

↓

If an expected exception occurs

↓

Handle it gracefully

---

# Dictionaries

Python dictionaries store:

```

key → value

```

Example:

```python
person = {
    "name": "Bob",
    "age": 61
}
```

Access:

```python
person["name"]
```

returns:

```python
Bob
```

---

# Mapping

Type hint:

```python
Mapping[str, Any]
```

Means:

A dictionary-like object whose

- keys are strings
- values may be any type

A normal Python dictionary satisfies this requirement.

---

# Any

```python
Any
```

Means:

Python accepts any type.

Examples:

- int
- float
- str
- bool
- dict
- list
- custom classes

Used mainly for type hints.

---

# Dictionary Comprehension

Creates a new dictionary from another dictionary.

Example:

```python
payload = {
    key: value
    for key, value in message.items()
    if key not in envelope_fields
}
```

Equivalent to:

```python
payload = {}

for key, value in message.items():
    if key not in envelope_fields:
        payload[key] = value
```

Dictionary comprehensions provide a concise way to filter or transform dictionaries.

---

# Type Hints

Type hints describe what types a function expects and returns. They improve readability, editor assistance, and static analysis but are **not enforced by Python at runtime**.

Examples:

```python
name: str
count: int
payload: dict[str, Any]
message: Mapping[str, Any]
```

They document your intent and make code easier to understand and maintain.