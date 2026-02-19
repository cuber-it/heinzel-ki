c.JupyterHub.ip = '0.0.0.0'
c.JupyterHub.port = 8000
c.JupyterHub.db_url = 'sqlite:////data/jupyterhub.sqlite'

import logging
c.JupyterHub.log_level = logging.INFO

# Einstieg: DummyAuthenticator — in Produktion ersetzen!
c.JupyterHub.authenticator_class = 'dummy'
c.DummyAuthenticator.password = 'changeme'
