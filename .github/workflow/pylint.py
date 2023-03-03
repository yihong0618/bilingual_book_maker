name: Pylint Workflow

on: [push]

jobs:
  pylint:
    name: Run Pylint
    runs-on: ubuntu-latest
    steps:
    - name: Checkout Code
      uses: actions/checkout@v2
    - name: Setup Python
      uses: actions/setup-python@v2
      with:
        python-version: '3.9'
    - name: Install Dependencies
      run: |
        python -m pip install --upgrade pip
        pip install pylint
        pip install -r requirements.txt
    - name: Run Pylint
      run: pylint make.py
