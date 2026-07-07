FROM cube-sandbox-int.tencentcloudcr.com/cube-sandbox/sandbox-code:latest

RUN pip install --no-cache-dir \
    pandas numpy matplotlib scikit-learn scipy \
    jupyter ipykernel seaborn plotly

RUN python -m ipykernel install --name python3
