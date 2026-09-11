# Official Apify base image: Python 3.13 + Playwright + Chromium preinstalled.
FROM apify/actor-python-playwright:3.13

COPY requirements.txt ./

RUN echo "Python version:" \
 && python --version \
 && echo "Pip version:" \
 && pip --version \
 && echo "Installing dependencies:" \
 && pip install -r requirements.txt \
 && echo "All installed Python packages:" \
 && pip freeze

COPY . ./

# Fail the build on a syntax error instead of failing the first run.
RUN python3 -m compileall -q src/

CMD ["python3", "-m", "src"]
