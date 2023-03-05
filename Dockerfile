FROM python:3.10-slim

RUN apt-get update

WORKDIR /app

COPY . .

RUN pip install -r /app/requirements.txt

ENTRYPOINT ["python3", "make_book.py"]