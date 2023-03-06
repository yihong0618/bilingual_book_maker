FROM python:3.10-slim

RUN apt-get update

WORKDIR /app

COPY requirements.txt .

RUN pip install -r /app/requirements.txt

COPY . .

ENTRYPOINT ["python3", "make_book.py"]