from setuptools import setup, find_packages

setup(
    name="hermes-middleware-sdk",
    version="1.0.0",
    description="Official Python SDK for the Hermes Webhook Delivery Middleware proxy.",
    long_description=open("README.md").read() if open("README.md") else "",
    long_description_content_type="text/markdown",
    author="Hermes Middleware Team",
    packages=find_packages(),
    python_requires=">=3.8",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
