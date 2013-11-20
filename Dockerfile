from ubuntu
MAINTAINER Charlie Lewis <charliel@lab41.org>

ENV REFRESHED_AT 2013-11-17
RUN sed 's/main$/main universe/' -i /etc/apt/sources.list
RUN apt-get update
RUN apt-get upgrade -y

# Keep upstart from complaining
RUN dpkg-divert --local --rename --add /sbin/initctl
RUN ln -s /bin/true /sbin/initctl

RUN apt-get install -y python-setuptools
RUN easy_install pip
ADD . /wharf
RUN pip install -r /wharf/requirements.txt

EXPOSE 5000

WORKDIR /wharf
CMD ["python", "api.py"]
