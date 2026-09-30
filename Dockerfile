# UniFleet v2 webapp — Railway production image and local dev image.
#
# Two stages, because Poetry must not share an environment with the app.
#
# The single-stage version installed Poetry into /usr/local's site-packages
# and then ran `poetry install` into that same site-packages. Poetry has its
# own dependency tree, and it overlaps ours — Poetry 2.4.1 pulls in
# charset-normalizer 3.5.1, our lock pins 3.4.3. So the install *downgraded*
# a package Poetry had just installed, in place. 3.5.1 ships a compiled
# `cd.cpython-311-x86_64-linux-gnu.so`; 3.4.3 ships a pure-Python `cd.py` and
# so has no .so to overwrite. When the stale extension survived, Python
# imported it in preference to the .py and `import requests` died with:
#
#   AttributeError: module 'charset_normalizer.md' has no attribute 'CharInfo'
#     File "src/charset_normalizer/cd.pyx", line 1, in init charset_normalizer.cd
#
# main.py's guarded import turned that into "Recipient management is
# unavailable" with all outbound email off. Whether the stale file survived
# depended on install ordering, so the same Dockerfile produced a working
# image locally and a broken one on Railway.
#
# The builder stage below installs the locked dependencies into /opt/venv,
# which Poetry never touches; the runtime stage copies that venv and does not
# contain Poetry at all. Nothing can downgrade anything in the final image.
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=2.4.1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

RUN pip install "poetry==${POETRY_VERSION}"

WORKDIR /app
COPY pyproject.toml poetry.lock ./

# VIRTUAL_ENV points Poetry at /opt/venv, so the locked dependency set lands
# there and Poetry's own tree stays behind in /usr/local. Only /opt/venv is
# copied forward.
RUN python -m venv /opt/venv \
 && VIRTUAL_ENV=/opt/venv poetry install --only main

# Fail the build here rather than boot a degraded app. This is the exact
# import that went down in production, and a broken dependency install must
# never get as far as a deploy.
RUN /opt/venv/bin/python -c "\
import requests, charset_normalizer; \
print('build check: requests', requests.__version__, \
      '| charset_normalizer', charset_normalizer.__version__)"


FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV=/opt/venv

# libfreetype6 is the runtime library Pillow uses to render voucher text in
# generate_voucher.py. It is what the retired Railway [nix] `freetype`
# package provided.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libfreetype6 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY . .

RUN mkdir -p /app/data/presets

EXPOSE 5000

# HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
#   CMD sh -c "python -c \"import urllib.request, os; urllib.request.urlopen(f'http://localhost:{os.environ.get(\\\"PORT\\\", \\\"5000\\\")}/healthz').read()\"" || exit 1

CMD ["sh", "-c", "python db/apply.py db/schema.sql db/seed_stations.sql db/seed_prices.sql && gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 1 main:app"]
