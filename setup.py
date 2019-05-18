from setuptools import setup, find_packages

setup(
    name='jupyterhub-repo2dockerspawner',
    version='0.1',
    packages=find_packages(),
    install_requires=[
        'dockerspawner',
        'jupyter-repo2docker'
    ],
)
