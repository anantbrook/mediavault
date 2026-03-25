# Dockerfile for MediaVault Flask Application

# Use the official Python image from the Docker Hub
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies (ffmpeg is required for yt-dlp)
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Copy the requirements file to the working directory
COPY requirements.txt .

# Install the required packages
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code to the container
COPY . .

# Expose the port the app runs on
EXPOSE 5050

# Tell the app it's running inside Docker
ENV DOCKER_ENV=1

# Command to run the application
CMD ["python", "app.py"]