# Use a lightweight Python base image
FROM python:3.10-slim

# Set environment variables
# PYTHONUNBUFFERED=1 ensures logs are visible in Northflank console immediately
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file first to leverage Docker caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code
COPY . .

# Create a non-root user for security (Recommended for production)
RUN adduser --disabled-password --gecos '' myuser
USER myuser

# Command to run the bot
# REPLACE 'main.py' with the actual name of your python file
CMD ["python", "main.py"]