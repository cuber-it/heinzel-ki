FROM jupyterhub/jupyterhub:latest

# Python-Kernel und häufig benötigte Pakete
RUN pip install --no-cache-dir \
    notebook \
    ipykernel \
    numpy \
    pandas \
    matplotlib

# Dummy-Authenticator für Entwicklung
RUN pip install --no-cache-dir jupyterhub-dummyauthenticator

# System-User anlegen (LocalProcessSpawner benötigt echte User)
RUN useradd -m -s /bin/bash jovyan
