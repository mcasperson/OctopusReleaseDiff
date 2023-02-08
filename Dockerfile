FROM python:3.9
ADD main.py .
ADD requirements.txt .
RUN pip install -r requirements.txt
ENTRYPOINT ["python", "./main.py"]