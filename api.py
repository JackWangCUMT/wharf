from flask import Flask
from flask import Markup
from flask import Response
from flask import jsonify
from flask import redirect
from flask import render_template
from flask import request
from flask import send_from_directory
from flask import session
from flask import url_for
from flask.ext.babel import Babel
from flask.ext.mail import Mail
from flask.ext.sqlalchemy import SQLAlchemy
from flask.ext.security import Security
from flask.ext.security import SQLAlchemyUserDatastore
from flask.ext.security import UserMixin
from flask.ext.security import RoleMixin
from functools import wraps
from werkzeug import secure_filename

from ast import literal_eval
from docker import client
from sh import git
from sh import mv
from shutil import rmtree

from os import environ
from os import listdir
from os import mkdir
from os import path
from os import remove
from os import rmdir
from os import walk

import redis
import requests
import tarfile
import time
import zipfile

#set defaults

DOMAIN = "localhost"
HIPACHE_PORT="80"
REDIS_HOST="localhost"
REDIS_PORT=6379
DOCKER_HOST="localhost"
ALLOWED_EXTENSIONS = set(['gz', 'zip'])
UPLOAD_FOLDER = '/home/vagrant/wharf/tmp/'
SERVICES_FOLDER = '/home/vagrant/wharf/services/'
SERVICE_DICT = {'description':'description.txt',
                'client':'client/client.txt',
                'about':'html/about.html',
                'body':'html/body.html',
                'link':'html/link.html',
                'dockerfile':'docker/Dockerfile'}

# environment variables, must be set in order for application to function
#try:
#    DOMAIN=environ["DOMAIN"]
#    REDIS_PORT=environ["REDIS_PORT"]
#    REDIS_HOST=environ["REDIS_HOST"]
#    HIPACHE_PORT=environ["HIPACHE_PORT"]
#    DOCKER_HOST=environ["DOCKER_HOST"]
#except Exception, e:
#    print e
#    print "environment not properly configured"
#    print environ
#    import sys; sys.exit(1)

r = redis.StrictRedis(host=REDIS_HOST, port=int(REDIS_PORT))
c = client.Client(version="1.6", base_url='http://%s:4243' % DOCKER_HOST)

app = Flask(__name__)
app.config['DEBUG'] = True
# this should be re-generated for production use
app.config['SECRET_KEY'] = 'EckNi2Fluincawd+'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////tmp/db.sqlite'
app.config['DEFAULT_MAIL_SENDER'] = 'dockerwharf@gmail.com'
app.config['SECURITY_REGISTERABLE'] = True
app.config['SECURITY_CONFIRMABLE'] = True
app.config['SECURITY_CHANGEABLE'] = True
app.config['SECURITY_RECOVERABLE'] = True
app.config['SECURITY_PASSWORD_HASH'] = 'sha512_crypt'
# this should be re-generated for production use
app.config['SECURITY_PASSWORD_SALT'] = 'S)1<P3_~$XF}DI=#'
app.config['SECURITY_POST_REGISTER_VIEW'] = '/login'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SERVICES_FOLDER'] = SERVICES_FOLDER
app.config.from_object('config.email')
app.debug = True

# Setup mail extension
mail = Mail(app)

# Setup babel
babel = Babel(app)

# Create database connection object
db = SQLAlchemy(app)

@babel.localeselector
def get_locale():
    override = request.args.get('lang')

    if override:
        session['lang'] = override

    rv = session.get('lang', 'en')
    return rv

# Define models
roles_users = db.Table('roles_users',
        db.Column('user_id', db.Integer(), db.ForeignKey('user.id')),
        db.Column('role_id', db.Integer(), db.ForeignKey('role.id')))

class Role(db.Model, RoleMixin):
    id = db.Column(db.Integer(), primary_key=True)
    name = db.Column(db.String(80), unique=True)
    description = db.Column(db.String(255))

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True)
    password = db.Column(db.String(255))
    active = db.Column(db.Boolean())
    confirmed_at = db.Column(db.DateTime())
    roles = db.relationship('Role', secondary=roles_users,
                            backref=db.backref('users', lazy='dynamic'))

    def __str__(self):
        return '<User id=%s email=%s>' % (self.id, self.email)

# Setup Flask-Security
user_datastore = SQLAlchemyUserDatastore(db, User, Role)
security = Security(app, user_datastore)

db.create_all()

def check_auth(username, password):
    """This function is called to check if a username /
    password combination is valid.
    """
    return username == 'admin' and password == 'secret' 

def authenticate():
    """Sends a 401 response that enables basic auth"""
    return Response(
    'Could not verify your access level for that URL.\n'
    'You have to login with proper credentials', 401,
    {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1] in ALLOWED_EXTENSIONS

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        url = ""
        file = ""
        desc = ""
        services = []
        try:
            url = request.form['wharf_url']
        except:
            url = ""
        try:
            file = request.files['file']
        except:
            file = ""
        if file != "":
            try:
                if file and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    file.save(path.join(app.config['UPLOAD_FOLDER'], filename))
                    if filename.rsplit('.', 1)[1] == "zip":
                        with zipfile.ZipFile(path.join(app.config['UPLOAD_FOLDER'], filename), 'r') as service_zip:
                            service_zip.extractall(path.join(app.config['UPLOAD_FOLDER'],
                                                             filename.rsplit('.', 1)[0]))
                            # !! TODO
                            #    allow exception for dockerfile, check at root as well
                            # check for existence of necessary files
                            missing_files = {}
                            for key,value in SERVICE_DICT.items():
                                if not path.exists(path.join(app.config['UPLOAD_FOLDER'],
                                                             filename.rsplit('.', 1)[0],
                                                             filename.rsplit('.', 1)[0],
                                                             value)):
                                    missing_files[key] = value
                            services.append(filename.rsplit('.', 1)[0])
                            if missing_files:
                                if "dockerfile" in missing_files:
                                    return render_template("failed.html")
                                else:
                                    return render_template("forms.html", services=services, missing_files=missing_files, filename=filename, indexDesc=desc, url=url)

                            # move to services folder
                            i = 0
                            while i != -1:
                                try:
                                    if i == 0:
                                        mv(path.join(app.config['UPLOAD_FOLDER'], 
                                                     filename.rsplit('.', 1)[0], 
                                                     filename.rsplit('.', 1)[0]),
                                           app.config['SERVICES_FOLDER'])
                                    elif i == 1:
                                        mv(path.join(app.config['UPLOAD_FOLDER'],
                                                     filename.rsplit('.', 1)[0], 
                                                     filename.rsplit('.', 1)[0]), 
                                           path.join(app.config['UPLOAD_FOLDER'], 
                                                     filename.rsplit('.', 1)[0], 
                                                     (filename.rsplit('.', 1)[0])+str(i)))
                                        mv(path.join(app.config['UPLOAD_FOLDER'], 
                                                     filename.rsplit('.', 1)[0], 
                                                     (filename.rsplit('.', 1)[0])+str(i)), 
                                           app.config['SERVICES_FOLDER'])
                                    else:
                                        mv(path.join(app.config['UPLOAD_FOLDER'], 
                                                     filename.rsplit('.', 1)[0], 
                                                     (filename.rsplit('.', 1)[0])+str(i-1)), 
                                           path.join(app.config['UPLOAD_FOLDER'], 
                                                     filename.rsplit('.', 1)[0], 
                                                     (filename.rsplit('.', 1)[0])+str(i)))
                                        mv(path.join(app.config['UPLOAD_FOLDER'], 
                                                     filename.rsplit('.', 1)[0], 
                                                     (filename.rsplit('.', 1)[0])+str(i)), 
                                           app.config['SERVICES_FOLDER'])
                                    i = -1
                                except:
                                    i += 1
                            # remove leftover files in tmp
                            remove(path.join(app.config['UPLOAD_FOLDER'], filename))
                            rmdir(path.join(app.config['UPLOAD_FOLDER'], filename.rsplit('.', 1)[0]))

                    elif filename.rsplit('.', 1)[1] == "gz":
                        with tarfile.open(path.join(app.config['UPLOAD_FOLDER'], filename)) as service_gz:
                            service_gz.extractall(path.join(app.config['UPLOAD_FOLDER'],
                                                            filename.rsplit('.', 2)[0]))
                            # !! TODO
                            #    allow exception for dockerfile, check at root as well
                            # check for existence of necessary files
                            missing_files = {}
                            for key,value in SERVICE_DICT.items():
                                if not path.exists(path.join(app.config['UPLOAD_FOLDER'],
                                                             filename.rsplit('.', 2)[0],
                                                             filename.rsplit('.', 2)[0],
                                                             value)):
                                    missing_files[key] = value
                            services.append(filename.rsplit('.', 2)[0])
                            if missing_files:
                                if "dockerfile" in missing_files:
                                    return render_template("failed.html")
                                else:
                                    return render_template("forms.html", services=services, missing_files=missing_files, filename=filename, indexDesc=desc, url=url)

                            # move to services folder
                            i = 0
                            while i != -1:
                                try:
                                    if i == 0:
                                        mv(path.join(app.config['UPLOAD_FOLDER'],
                                                     filename.rsplit('.', 2)[0],
                                                     filename.rsplit('.', 2)[0]),
                                           app.config['SERVICES_FOLDER'])
                                    elif i == 1:
                                        mv(path.join(app.config['UPLOAD_FOLDER'],
                                                     filename.rsplit('.', 2)[0], 
                                                     filename.rsplit('.', 2)[0]), 
                                           path.join(app.config['UPLOAD_FOLDER'], 
                                                     filename.rsplit('.', 2)[0], 
                                                     (filename.rsplit('.', 2)[0])+str(i)))
                                        mv(path.join(app.config['UPLOAD_FOLDER'],
                                                     filename.rsplit('.', 2)[0], 
                                                     (filename.rsplit('.', 2)[0])+str(i)), 
                                           app.config['SERVICES_FOLDER'])
                                    else:
                                        mv(path.join(app.config['UPLOAD_FOLDER'], 
                                                     filename.rsplit('.', 2)[0], 
                                                     (filename.rsplit('.', 2)[0])+str(i-1)), 
                                           path.join(app.config['UPLOAD_FOLDER'],
                                                     filename.rsplit('.', 2)[0], 
                                                     (filename.rsplit('.', 2)[0])+str(i)))
                                        mv(path.join(app.config['UPLOAD_FOLDER'], 
                                                     filename.rsplit('.', 2)[0], 
                                                     (filename.rsplit('.', 2)[0])+str(i)), 
                                           app.config['SERVICES_FOLDER'])
                                    i = -1
                                except:
                                    i += 1
                            # remove leftover files in tmp
                            remove(path.join(app.config['UPLOAD_FOLDER'], filename))
                            rmdir(path.join(app.config['UPLOAD_FOLDER'], filename.rsplit('.', 2)[0]))
                    else:
                        return render_template("failed.html")
                    # !! TODO
                    #    some post-processing once the file is uploaded
                else:
                    return render_template("failed.html")
            except:
                print "No file selected"
        elif url != "":
            try:
                if url:
                    # !! TODO try/except
                    if url.rsplit('.', 1)[1] == "git":
                        # !! TODO try/except - if the folder already exists
                        git.clone(url, path.join(app.config['UPLOAD_FOLDER'],
                                                 (url.rsplit('/', 1)[1]).rsplit('.', 1)[0]))

                        # check for dockerfile at root
                        if path.exists(path.join(app.config['UPLOAD_FOLDER'],
                                                 (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                 "Dockerfile")):
                            # check for existence of necessary files
                            missing_files = {}
                            for key,value in SERVICE_DICT.items():
                                if not path.exists(path.join(app.config['UPLOAD_FOLDER'],
                                                             (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                             value)):
                                    missing_files[key] = value
                            services.append((url.rsplit('/', 1)[1]).rsplit('.', 1)[0])
                            if "dockerfile" in missing_files:
                                del missing_files['dockerfile']
                            if missing_files:
                                return render_template("forms.html",
                                                       services=services,
                                                       missing_files=missing_files,
                                                       filename=file,
                                                       indexDesc=desc,
                                                       url=url)
                            # move to services folder
                            i = 0
                            while i != -1:
                                try:
                                    if i == 0:
                                        mv(path.join(app.config['UPLOAD_FOLDER'],
                                                     (url.rsplit('/', 1)[1]).rsplit('.', 1)[0]),
                                           app.config['SERVICES_FOLDER'])
                                    elif i == 1:
                                        mv(path.join(app.config['UPLOAD_FOLDER'],
                                                     (url.rsplit('/', 1)[1]).rsplit('.', 1)[0]),
                                           path.join(app.config['UPLOAD_FOLDER'], 
                                                     ((url.rsplit('/', 1)[1]).rsplit('.', 1)[0])+str(i)))
                                        mv(path.join(app.config['UPLOAD_FOLDER'],
                                                     ((url.rsplit('/', 1)[1]).rsplit('.', 1)[0])+str(i)),
                                           app.config['SERVICES_FOLDER'])
                                    else:
                                        mv(path.join(app.config['UPLOAD_FOLDER'], 
                                                     ((url.rsplit('/', 1)[1]).rsplit('.', 1)[0])+str(i-1)),
                                           path.join(app.config['UPLOAD_FOLDER'],
                                                     ((url.rsplit('/', 1)[1]).rsplit('.', 1)[0])+str(i)))
                                        mv(path.join(app.config['UPLOAD_FOLDER'], 
                                                     ((url.rsplit('/', 1)[1]).rsplit('.', 1)[0])+str(i)),
                                           app.config['SERVICES_FOLDER'])
                                    i = -1
                                except:
                                    i += 1
                            try:
                                # remove leftover files in tmp
                                rmdir(path.join(app.config['UPLOAD_FOLDER'],
                                                (url.rsplit('/', 1)[1]).rsplit('.', 1)[0]))
                            except:
                                pass
                        # check for dockerfile assuming repo is the services folder
                        elif path.exists(path.join(app.config['UPLOAD_FOLDER'],
                                                 (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                 SERVICE_DICT['dockerfile'])):
                            # check for existence of necessary files
                            missing_files = {}
                            for key,value in SERVICE_DICT.items():
                                if not path.exists(path.join(app.config['UPLOAD_FOLDER'],
                                                             (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                             value)):
                                    missing_files[key] = value
                            services.append((url.rsplit('/', 1)[1]).rsplit('.', 1)[0])
                            if missing_files:
                                return render_template("forms.html",
                                                       services=services,
                                                       missing_files=missing_files,
                                                       filename=file,
                                                       indexDesc=desc,
                                                       url=url)
                            # move to services folder
                            i = 0
                            while i != -1:
                                try:
                                    if i == 0:
                                        mv(path.join(app.config['UPLOAD_FOLDER'],
                                                     (url.rsplit('/', 1)[1]).rsplit('.', 1)[0]),
                                           app.config['SERVICES_FOLDER'])
                                    elif i == 1:
                                        mv(path.join(app.config['UPLOAD_FOLDER'],
                                                     (url.rsplit('/', 1)[1]).rsplit('.', 1)[0]),
                                           path.join(app.config['UPLOAD_FOLDER'], 
                                                     ((url.rsplit('/', 1)[1]).rsplit('.', 1)[0])+str(i)))
                                        mv(path.join(app.config['UPLOAD_FOLDER'],
                                                     ((url.rsplit('/', 1)[1]).rsplit('.', 1)[0])+str(i)),
                                           app.config['SERVICES_FOLDER'])
                                    else:
                                        mv(path.join(app.config['UPLOAD_FOLDER'], 
                                                     ((url.rsplit('/', 1)[1]).rsplit('.', 1)[0])+str(i-1)),
                                           path.join(app.config['UPLOAD_FOLDER'],
                                                     ((url.rsplit('/', 1)[1]).rsplit('.', 1)[0])+str(i)))
                                        mv(path.join(app.config['UPLOAD_FOLDER'], 
                                                     ((url.rsplit('/', 1)[1]).rsplit('.', 1)[0])+str(i)),
                                           app.config['SERVICES_FOLDER'])
                                    i = -1
                                except:
                                    i += 1
                            try:
                                # remove leftover files in tmp
                                rmdir(path.join(app.config['UPLOAD_FOLDER'],
                                                (url.rsplit('/', 1)[1]).rsplit('.', 1)[0]))
                            except:
                                pass
                        else:
                            i = 0
                            repo_dirs = []
                            for root, dirs, files in walk(path.join(app.config['UPLOAD_FOLDER'],
                                                                    (url.rsplit('/', 1)[1]).rsplit('.', 1)[0])):
                                if i == 0:
                                    repo_dirs = dirs
                                i += 1
                            if ".git" in repo_dirs:
                                repo_dirs.remove(".git")
                            services=repo_dirs
                            for service_dir in repo_dirs:
                                # check for dockerfile one folder deep
                                # could be more than one
                                if path.exists(path.join(app.config['UPLOAD_FOLDER'],
                                                         (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                         service_dir, "Dockerfile")):
                                    # check for existence of necessary files
                                    missing_files = {}
                                    for key,value in SERVICE_DICT.items():
                                        if not path.exists(path.join(app.config['UPLOAD_FOLDER'],
                                                                     (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                                     service_dir,
                                                                     value)):
                                            missing_files[key] = value

                                    if "dockerfile" in missing_files:
                                        del missing_files['dockerfile']
                                    if missing_files:
                                        # !! TODO TODO TODO
                                        #    this needs to be re-worked for times 
                                        #    when there is more than one service_dir
                                        return render_template("forms.html",
                                                               services=services,
                                                               missing_files=missing_files,
                                                               filename=file,
                                                               indexDesc=desc,
                                                               url=url)
                                    # move to services folder
                                    i = 0
                                    while i != -1:
                                        try:
                                            if i == 0:
                                                mv(path.join(app.config['UPLOAD_FOLDER'],
                                                             (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                             service_dir),
                                                   app.config['SERVICES_FOLDER'])
                                            elif i == 1:
                                                mv(path.join(app.config['UPLOAD_FOLDER'],
                                                             (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                             service_dir),
                                                   path.join(app.config['UPLOAD_FOLDER'], 
                                                             (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                             service_dir+str(i)))
                                                mv(path.join(app.config['UPLOAD_FOLDER'],
                                                             (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                             service_dir+str(i)),
                                                   app.config['SERVICES_FOLDER'])
                                            else:
                                                mv(path.join(app.config['UPLOAD_FOLDER'], 
                                                             (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                             service_dir+str(i-1)),
                                                   path.join(app.config['UPLOAD_FOLDER'],
                                                             (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                             service_dir+str(i)))
                                                mv(path.join(app.config['UPLOAD_FOLDER'], 
                                                             (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                             service_dir+str(i)),
                                                   app.config['SERVICES_FOLDER'])
                                            i = -1
                                        except:
                                            i += 1
                                # check for dockerfile in regular services folder
                                # could be more than one
                                elif path.exists(path.join(app.config['UPLOAD_FOLDER'],
                                                           (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                           service_dir, SERVICE_DICT['dockerfile'])):
                                    print "dockerfile in services folder"
                                    # check for existence of necessary files
                                    missing_files = {}
                                    for key,value in SERVICE_DICT.items():
                                        if not path.exists(path.join(app.config['UPLOAD_FOLDER'],
                                                                     (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                                     service_dir,
                                                                     value)):
                                            missing_files[key] = value

                                    if "dockerfile" in missing_files:
                                        del missing_files['dockerfile']
                                    if missing_files:
                                        # !! TODO TODO TODO
                                        #    this needs to be re-worked for times 
                                        #    when there is more than one service_dir
                                        return render_template("forms.html",
                                                               services=services,
                                                               missing_files=missing_files,
                                                               filename=file,
                                                               indexDesc=desc,
                                                               url=url)
                                    # move to services folder
                                    i = 0
                                    while i != -1:
                                        try:
                                            if i == 0:
                                                print path.join(app.config['UPLOAD_FOLDER'],
                                                                (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                                service_dir)
                                                mv(path.join(app.config['UPLOAD_FOLDER'],
                                                             (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                             service_dir),
                                                   app.config['SERVICES_FOLDER'])
                                            elif i == 1:
                                                mv(path.join(app.config['UPLOAD_FOLDER'],
                                                             (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                             service_dir),
                                                   path.join(app.config['UPLOAD_FOLDER'], 
                                                             (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                             service_dir+str(i)))
                                                mv(path.join(app.config['UPLOAD_FOLDER'],
                                                             (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                             service_dir+str(i)),
                                                   app.config['SERVICES_FOLDER'])
                                            else:
                                                mv(path.join(app.config['UPLOAD_FOLDER'], 
                                                             (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                             service_dir+str(i-1)),
                                                   path.join(app.config['UPLOAD_FOLDER'],
                                                             (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                             service_dir+str(i)))
                                                mv(path.join(app.config['UPLOAD_FOLDER'], 
                                                             (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                             service_dir+str(i)),
                                                   app.config['SERVICES_FOLDER'])
                                            i = -1
                                        except:
                                            i += 1
                            try:
                                rmdir(path.join(app.config['UPLOAD_FOLDER'],
                                                (url.rsplit('/', 1)[1]).rsplit('.', 1)[0]))
                            except:
                                pass
                    else:
                        # !! TODO
                        #    should point to docker index url, expects a <meta name="description"
                        #    won't have a dockerfile in the service folder
                        #    note the naming scheme will mess with directory structure of service name
                        #    needs to be handled as a special case
                        repo = ""
                        desc = ""
                        try:
                            index_repo = (requests.get(url).content).split('<meta name="description" content="')
                            index_repo = index_repo[1].split("\" />")
                            # !! TODO try, if fails, there is no description.
                            try:
                                repo, desc = index_repo[0].split(": ", 1)
                                desc = desc.replace("\n", " ")
                                print repo, desc
                            except:
                                repo = index_repo[0]
                                print repo
                        except:
                            return render_template("failed.html")
                        if repo == "":
                            return render_template("failed.html")
                        missing_files = {}
                        for key,value in SERVICE_DICT.items():
                            missing_files[key] = value
                        del missing_files["dockerfile"]
                        if desc != "":
                            del missing_files["description"]
                        services.append(repo)

                        return render_template("forms.html",
                                               services=services,
                                               missing_files=missing_files,
                                               filename=file,
                                               indexDesc=desc,
                                               url=url)
                        print url
            except:
                print "Bad URL"
        else:
            return render_template("failed.html")
        return redirect(url_for('index'))

    row = ""
    services = listdir("services")
    for service in services:
        last_modified = ""
        last_modified = time.ctime(path.getmtime("services/"+service))
        description = ""
        # !! TODO try/except
        description_path = "services/"+service+"/"+SERVICE_DICT['description']
        with open(description_path, 'r') as content_file:
            description = content_file.read()
        row += '<tr><td class="rowlink-skip"><a href="saas/'+service+'">'+service+'</a></td><td>'+description+'</td><td><a href="saas/'+service+'">'+last_modified+'</a></td></tr>'
    row = Markup(row)
    return render_template("index.html",row=row)

@app.route('/saas/<service>')
def saas(service):
    about = ""
    body = ""
    link = ""
    link_name = ""
    # !! TODO try/except
    about_path = "services/"+service+"/"+SERVICE_DICT['about']
    with open(about_path, 'r') as content_file:
        about = content_file.read()
    body_path = "services/"+service+"/"+SERVICE_DICT['body']
    with open(body_path, 'r') as content_file:
        body = content_file.read()
    link_path = "services/"+service+"/"+SERVICE_DICT['link']
    with open(link_path, 'r') as content_file:
        link = content_file.read()
    link_a = link.split(" ", 1)
    link = link_a[0]
    link_name = link_a[1]
    return render_template("saas.html",service=service,about=about,body=body,link=link,link_name=link_name)

@app.route('/new/<service>', methods=["POST"])
def new(service):
    exposed_ports = []
    # !! TODO try/expect
    dockerfile = ""
    docker_path = ""
    if path.exists(path.join(app.config['SERVICES_FOLDER'],
                             service,
                             SERVICE_DICT['dockerfile'])):
        dockerfile = "services/"+service+"/"+SERVICE_DICT['dockerfile']
        docker_path = "services/"+service+"/docker/"
    elif path.exists(path.join(app.config['SERVICES_FOLDER'],
                             service,
                             "Dockerfile")):
        dockerfile = "services/"+service+"/Dockerfile"
        docker_path = "services/"+service+"/"
    # if dockerfile is still ""
    # docker index
    else:
        # !! TODO try/except
        service = service.replace("-", "/", 1)
        c.pull(service)
        container = c.create_container(service)
        container_id = container["Id"]
        c.start(container)
        b = c.inspect_container(container)
        ports = b['NetworkSettings']['PortMapping']['Tcp']
        for key,value in ports.items():
            exposed_ports.append(key)
        for exposed_port in exposed_ports:
            container_port = c.port(container_id, exposed_port)
            r.rpush("frontend:%s.%s" % (container_id, DOMAIN), container_id)
            r.rpush("frontend:%s.%s" % (container_id, DOMAIN), "http://%s:%s" %(DOMAIN, container_port))
            # !! TODO more than one url when there is more than one exposed_port
            if HIPACHE_PORT == "80":
                url = "%s:%s" % (DOMAIN, container_port)
            else:
                url = "%s:%s" % (DOMAIN, container_port)
        return jsonify(url=url)

    with open(dockerfile, 'r') as content_file:
        for line in content_file:
            if line.startswith("EXPOSE"):
                line = line.strip()
                line_a = line.split(" ")
                for port in line_a[1:]:
                    exposed_ports.append(port)
    container = []
    try:
        image_id = "JUNK"
        image_id_path = "services/"+service+"/.image_id"
        with open(image_id_path, 'r') as content_file:
            image_id = content_file.read()
        container = c.create_container(image_id, ports=exposed_ports)
    except:
        # !! TODO try/except
        image_id, response = c.build(path=docker_path, tag=service)
        # !! TODO leaving in for debugging for now
        print image_id, response
        image_id_path = "services/"+service+"/.image_id"
        with open(image_id_path, 'w') as content_file:
            content_file.write(image_id)
        container = c.create_container(image_id, ports=exposed_ports)
    container_id = container["Id"]
    #bindings = {}
    #for exposed_port in exposed_ports:
    #    port = exposed_port+"/tcp"
    #    bindings[port] = [{'HostIp': '', 'HostPort': ''}]
    #c.start(container_id, port_bindings=bindings)
    c.start(container_id)
    for exposed_port in exposed_ports:
        container_port = c.port(container_id, exposed_port)
        r.rpush("frontend:%s.%s" % (container_id, DOMAIN), container_id)
        r.rpush("frontend:%s.%s" % (container_id, DOMAIN), "http://%s:%s" %(DOMAIN, container_port))
        # !! TODO more than one url when there is more than one exposed_port
        if HIPACHE_PORT == "80":
            url = "%s:%s" % (DOMAIN, container_port)
        else:
            url = "%s:%s" % (DOMAIN, container_port)

    return jsonify(url=url)

@app.route('/details/<service>/<url>')
def details(url, service):
    # !! TODO try/except
    client = ""
    test = ""
    link = ""
    link_name = ""
    link_path = "services/"+service+"/"+SERVICE_DICT['link']
    with open(link_path, 'r') as content_file:
        link = content_file.read()
    link_a = link.split(" ", 1)
    link = link_a[0]
    link_name = link_a[1]
    client_path = "services/"+service+"/"+SERVICE_DICT['client']
    with open(client_path, 'r') as content_file:
        client = content_file.read()
    client_a = client.split("\n")
    client = client_a[0]
    test_path = "services/"+service+"/client/"+client_a[1]
    with open(test_path, 'r') as content_file:
        test = content_file.read()
    return render_template("details.html",
                           url=url,
                           service=service,
                           client=client,
                           test=test,
                           link=link,
                           link_name=link_name)

@app.route('/forms', methods=['POST'])
def forms():
    # !! TODO try/except
    try:
        filename = request.json['filename']
        url = request.json['url']
        services = request.json['services']
        i = 0
        j = 0
        if filename:
            if filename.rsplit('.', 1)[1] == "zip":
                # move to services folder
                i = 0
                j = 0
                while i != -1:
                    try:
                        if i == 0:
                            mv(path.join(app.config['UPLOAD_FOLDER'], 
                                         filename.rsplit('.', 1)[0], 
                                         filename.rsplit('.', 1)[0]),
                               app.config['SERVICES_FOLDER'])
                        elif i == 1:
                            mv(path.join(app.config['UPLOAD_FOLDER'],
                                         filename.rsplit('.', 1)[0], 
                                         filename.rsplit('.', 1)[0]), 
                               path.join(app.config['UPLOAD_FOLDER'], 
                                         filename.rsplit('.', 1)[0], 
                                         (filename.rsplit('.', 1)[0])+str(i)))
                            mv(path.join(app.config['UPLOAD_FOLDER'], 
                                         filename.rsplit('.', 1)[0], 
                                         (filename.rsplit('.', 1)[0])+str(i)), 
                               app.config['SERVICES_FOLDER'])
                        else:
                            mv(path.join(app.config['UPLOAD_FOLDER'], 
                                         filename.rsplit('.', 1)[0], 
                                         (filename.rsplit('.', 1)[0])+str(i-1)), 
                               path.join(app.config['UPLOAD_FOLDER'], 
                                         filename.rsplit('.', 1)[0], 
                                         (filename.rsplit('.', 1)[0])+str(i)))
                            mv(path.join(app.config['UPLOAD_FOLDER'], 
                                         filename.rsplit('.', 1)[0], 
                                         (filename.rsplit('.', 1)[0])+str(i)), 
                               app.config['SERVICES_FOLDER'])
                        j = i
                        i = -1
                    except:
                        i += 1
                # remove leftover files in tmp
                remove(path.join(app.config['UPLOAD_FOLDER'], filename))
                rmtree(path.join(app.config['UPLOAD_FOLDER'], filename.rsplit('.', 1)[0]))

            elif filename.rsplit('.', 1)[1] == "gz":
                # move to services folder
                i = 0
                j = 0
                while i != -1:
                    try:
                        if i == 0:
                            mv(path.join(app.config['UPLOAD_FOLDER'], 
                                         filename.rsplit('.', 2)[0], 
                                         filename.rsplit('.', 2)[0]),
                               app.config['SERVICES_FOLDER'])
                        elif i == 1:
                            mv(path.join(app.config['UPLOAD_FOLDER'],
                                         filename.rsplit('.', 2)[0], 
                                         filename.rsplit('.', 2)[0]), 
                               path.join(app.config['UPLOAD_FOLDER'], 
                                         filename.rsplit('.', 2)[0], 
                                         (filename.rsplit('.', 2)[0])+str(i)))
                            mv(path.join(app.config['UPLOAD_FOLDER'], 
                                         filename.rsplit('.', 2)[0], 
                                         (filename.rsplit('.', 2)[0])+str(i)), 
                               app.config['SERVICES_FOLDER'])
                        else:
                            mv(path.join(app.config['UPLOAD_FOLDER'], 
                                         filename.rsplit('.', 2)[0], 
                                         (filename.rsplit('.', 2)[0])+str(i-1)), 
                               path.join(app.config['UPLOAD_FOLDER'], 
                                         filename.rsplit('.', 2)[0], 
                                         (filename.rsplit('.', 2)[0])+str(i)))
                            mv(path.join(app.config['UPLOAD_FOLDER'], 
                                         filename.rsplit('.', 2)[0], 
                                         (filename.rsplit('.', 2)[0])+str(i)), 
                               app.config['SERVICES_FOLDER'])
                        j = i
                        i = -1
                    except:
                        i += 1
                # remove leftover files in tmp
                remove(path.join(app.config['UPLOAD_FOLDER'], filename))
                rmtree(path.join(app.config['UPLOAD_FOLDER'], filename.rsplit('.', 2)[0]))
        
            missing_files = request.json['missing_files']
            if "description" in missing_files:
                description = ""
                try:
                    description = request.json['description']
                except:
                    pass
                if j == 0:
                    j = ""
                if filename.rsplit('.', 1)[1] == "zip": 
                    with open(app.config['SERVICES_FOLDER']+filename.rsplit('.', 1)[0]+str(j)+"/"+SERVICE_DICT['description'], 'w') as f:
                        f.write(description)
                elif filename.rsplit('.', 1)[1] == "gz":
                    with open(app.config['SERVICES_FOLDER']+filename.rsplit('.', 2)[0]+str(j)+"/"+SERVICE_DICT['description'], 'w') as f:
                        f.write(description)
            if "client" in missing_files:
                client = ""
                clientLanguage = ""
                clientFilename = "dummy.txt"
                try:
                    client = request.json['client']
                    clientLanguage = request.json['clientLanguage']
                    clientFilename = request.json['clientFilename']
                except:
                    pass
                if j == 0:
                    j = ""
                if filename.rsplit('.', 1)[1] == "zip": 
                    if not path.exists(app.config['SERVICES_FOLDER']+filename.rsplit('.', 1)[0]+str(j)+"/client"):
                        mkdir(app.config['SERVICES_FOLDER']+filename.rsplit('.', 1)[0]+str(j)+"/client")
                    with open(app.config['SERVICES_FOLDER']+filename.rsplit('.', 1)[0]+str(j)+"/"+SERVICE_DICT['client'], 'w') as f:
                        f.write(clientLanguage+"\n")
                        f.write(clientFilename)
                    with open(app.config['SERVICES_FOLDER']+filename.rsplit('.', 1)[0]+str(j)+"/client/"+clientFilename, 'w') as f:
                        f.write(client)
                elif filename.rsplit('.', 1)[1] == "gz":
                    if not path.exists(app.config['SERVICES_FOLDER']+filename.rsplit('.', 2)[0]+str(j)+"/client"):
                        mkdir(app.config['SERVICES_FOLDER']+filename.rsplit('.', 2)[0]+str(j)+"/client")
                    with open(app.config['SERVICES_FOLDER']+filename.rsplit('.', 2)[0]+str(j)+"/"+SERVICE_DICT['client'], 'w') as f:
                        f.write(clientLanguage+"\n")
                        f.write(clientFilename)
                    with open(app.config['SERVICES_FOLDER']+filename.rsplit('.', 2)[0]+str(j)+"/client/"+clientFilename, 'w') as f:
                        f.write(client)
            if "about" in missing_files:
                about = ""
                try:
                    about = request.json['about']
                except:
                    pass
                if j == 0:
                    j = ""
                if filename.rsplit('.', 1)[1] == "zip": 
                    if not path.exists(app.config['SERVICES_FOLDER']+filename.rsplit('.', 1)[0]+str(j)+"/html"):
                        mkdir(app.config['SERVICES_FOLDER']+filename.rsplit('.', 1)[0]+str(j)+"/html")
                    with open(app.config['SERVICES_FOLDER']+filename.rsplit('.', 1)[0]+str(j)+"/"+SERVICE_DICT['about'], 'w') as f:
                        f.write(about)
                elif filename.rsplit('.', 1)[1] == "gz":
                    if not path.exists(app.config['SERVICES_FOLDER']+filename.rsplit('.', 2)[0]+str(j)+"/html"):
                        mkdir(app.config['SERVICES_FOLDER']+filename.rsplit('.', 2)[0]+str(j)+"/html")
                    with open(app.config['SERVICES_FOLDER']+filename.rsplit('.', 2)[0]+str(j)+"/"+SERVICE_DICT['about'], 'w') as f:
                        f.write(about)
            if "body" in missing_files:
                body = ""
                try:
                    body = request.json['body']
                except:
                    pass
                if j == 0:
                    j = ""
                if filename.rsplit('.', 1)[1] == "zip": 
                    if not path.exists(app.config['SERVICES_FOLDER']+filename.rsplit('.', 1)[0]+str(j)+"/html"):
                        mkdir(app.config['SERVICES_FOLDER']+filename.rsplit('.', 1)[0]+str(j)+"/html")
                    with open(app.config['SERVICES_FOLDER']+filename.rsplit('.', 1)[0]+str(j)+"/"+SERVICE_DICT['body'], 'w') as f:
                        f.write(body)
                elif filename.rsplit('.', 1)[1] == "gz":
                    if not path.exists(app.config['SERVICES_FOLDER']+filename.rsplit('.', 2)[0]+str(j)+"/html"):
                        mkdir(app.config['SERVICES_FOLDER']+filename.rsplit('.', 2)[0]+str(j)+"/html")
                    with open(app.config['SERVICES_FOLDER']+filename.rsplit('.', 2)[0]+str(j)+"/"+SERVICE_DICT['body'], 'w') as f:
                        f.write(body)
            if "link" in missing_files:
                link = "#"
                linkName = "None"
                try:
                    link = request.json['link']
                    linkName = request.json['linkName']
                except:
                    pass
                if j == 0:
                    j = ""
                if filename.rsplit('.', 1)[1] == "zip": 
                    if not path.exists(app.config['SERVICES_FOLDER']+filename.rsplit('.', 1)[0]+str(j)+"/html"):
                        mkdir(app.config['SERVICES_FOLDER']+filename.rsplit('.', 1)[0]+str(j)+"/html")
                    with open(app.config['SERVICES_FOLDER']+filename.rsplit('.', 1)[0]+str(j)+"/"+SERVICE_DICT['link'], 'w') as f:
                        f.write(link+" "+linkName)
                elif filename.rsplit('.', 1)[1] == "gz":
                    if not path.exists(app.config['SERVICES_FOLDER']+filename.rsplit('.', 2)[0]+str(j)+"/html"):
                        mkdir(app.config['SERVICES_FOLDER']+filename.rsplit('.', 2)[0]+str(j)+"/html")
                    with open(app.config['SERVICES_FOLDER']+filename.rsplit('.', 2)[0]+str(j)+"/"+SERVICE_DICT['link'], 'w') as f:
                        f.write(link+" "+linkName)
        elif url:
            j_array = []
            if not "." in url or not "git" in url:
                # docker index
                j = 0
                j_array.append(j)
            elif url.rsplit('.', 1)[1] == "git":
                # move to services folder
                i = 0
                # keeps track of the number of the service (if there is more than one)
                j = 0
                services = services.replace('&#39;', "'")
                services = [ item.encode('ascii') for item in literal_eval(services) ]
                if not services:
                    return render_template("failed.html")
                elif len(services) == 1:
                    while i != -1:
                        try:
                            if i == 0:
                                mv(path.join(app.config['UPLOAD_FOLDER'],
                                             (url.rsplit('/', 1)[1]).rsplit('.', 1)[0]),
                                   app.config['SERVICES_FOLDER'])
                            elif i == 1:
                                mv(path.join(app.config['UPLOAD_FOLDER'],
                                             (url.rsplit('/', 1)[1]).rsplit('.', 1)[0]),
                                   path.join(app.config['UPLOAD_FOLDER'], 
                                             ((url.rsplit('/', 1)[1]).rsplit('.', 1)[0])+str(i)))
                                mv(path.join(app.config['UPLOAD_FOLDER'],
                                             ((url.rsplit('/', 1)[1]).rsplit('.', 1)[0])+str(i)),
                                   app.config['SERVICES_FOLDER'])
                            else:
                                mv(path.join(app.config['UPLOAD_FOLDER'], 
                                             ((url.rsplit('/', 1)[1]).rsplit('.', 1)[0])+str(i-1)),
                                   path.join(app.config['UPLOAD_FOLDER'],
                                             ((url.rsplit('/', 1)[1]).rsplit('.', 1)[0])+str(i)))
                                mv(path.join(app.config['UPLOAD_FOLDER'], 
                                             ((url.rsplit('/', 1)[1]).rsplit('.', 1)[0])+str(i)),
                                   app.config['SERVICES_FOLDER'])
                            j = i
                            i = -1
                        except:
                            i += 1
                    try:
                        # remove leftover files in tmp
                        rmdir(path.join(app.config['UPLOAD_FOLDER'],
                                        (url.rsplit('/', 1)[1]).rsplit('.', 1)[0]))
                    except:
                        pass
                else:
                    for service in services:
                        i = 0
                        while i != -1:
                            try:
                                if i == 0:
                                    mv(path.join(app.config['UPLOAD_FOLDER'],
                                                 (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                 service),
                                       app.config['SERVICES_FOLDER'])
                                elif i == 1:
                                    mv(path.join(app.config['UPLOAD_FOLDER'],
                                                 (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                 service),
                                       path.join(app.config['UPLOAD_FOLDER'], 
                                                 (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                 (service)+str(i)))
                                    mv(path.join(app.config['UPLOAD_FOLDER'],
                                                 (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                 (service)+str(i)),
                                       app.config['SERVICES_FOLDER'])
                                else:
                                    mv(path.join(app.config['UPLOAD_FOLDER'], 
                                                 (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                 (service)+str(i-1)),
                                       path.join(app.config['UPLOAD_FOLDER'],
                                                 (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                 (service)+str(i)))
                                    mv(path.join(app.config['UPLOAD_FOLDER'], 
                                                 (url.rsplit('/', 1)[1]).rsplit('.', 1)[0],
                                                 (service)+str(i)),
                                       app.config['SERVICES_FOLDER'])
                                j = i
                                i = -1
                            except:
                                i += 1
                        j_array.append(j)
                    try:
                        # remove leftover files in tmp
                        rmtree(path.join(app.config['UPLOAD_FOLDER'],
                                        (url.rsplit('/', 1)[1]).rsplit('.', 1)[0]))
                    except:
                        pass

                # !! TODO
                # array of services
                # return array of missing files, empty slots for ones that don't need replacing
                # eventually allow this for file upload as well
                # something different is git repo versus docker index
                # can all git repos be handled the same, or are there ones that might be different?

            services = services.replace('&#39;', "'")
            services = [ item.encode('ascii') for item in literal_eval(services) ]
            if len(services) > 1:
                print services
                counter = 0
                for service in services:
                    # update missing_files for array of them,
                    # similarly with description, client, about, body, link, etc.
                    missing_files = request.json['missing_files']
                    description = ""
                    if "description" in missing_files:
                        try:
                            description = request.json['description']
                        except:
                            pass
                        if j_array[counter] == 0:
                            j_array[counter] = ""
                        # if url is docker index
                        if not "." in url or not "git" in url:
                            if description == "":
                                description = request.json['indexDesc']
                            index_service = service.replace("/", "-")
                            if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])):
                                mkdir(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter]))
                            with open(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])+"/"+SERVICE_DICT['description'], 'w') as f:
                                f.write(description)
                        elif url.rsplit('.', 1)[1] == "git": 
                            with open(app.config['SERVICES_FOLDER']+service+str(j_array[counter])+"/"+SERVICE_DICT['description'], 'w') as f:
                                f.write(description)
                    else:
                        if j_array[counter] == 0:
                            j_array[counter] = ""
                        # if url is docker index
                        if not "." in url or not "git" in url:
                            if description == "":
                                description = request.json['indexDesc']
                            index_service = service.replace("/", "-")
                            if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])):
                                mkdir(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter]))
                            with open(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])+"/"+SERVICE_DICT['description'], 'w') as f:
                                f.write(description)
                       
                    if "client" in missing_files:
                        client = ""
                        clientLanguage = ""
                        clientFilename = "dummy.txt"
                        try:
                            client = request.json['client']
                            clientLanguage = request.json['clientLanguage']
                            clientFilename = request.json['clientFilename']
                        except:
                            pass
                        if j_array[counter] == 0:
                            j_array[counter] = ""
                        # if url is docker index
                        if not "." in url or not "git" in url:
                            index_service = service.replace("/", "-")
                            if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])):
                                mkdir(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter]))
                            if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])+"/client"):
                                mkdir(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])+"/client")
                            with open(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])+"/"+SERVICE_DICT['client'], 'w') as f:
                                f.write(clientLanguage+"\n")
                                f.write(clientFilename)
                            with open(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])+"/client/"+clientFilename, 'w') as f:
                                f.write(client)
                        elif url.rsplit('.', 1)[1] == "git": 
                            if not path.exists(app.config['SERVICES_FOLDER']+service+str(j_array[counter])+"/client"):
                                mkdir(app.config['SERVICES_FOLDER']+service+str(j_array[counter])+"/client")
                            with open(app.config['SERVICES_FOLDER']+service+str(j_array[counter])+"/"+SERVICE_DICT['client'], 'w') as f:
                                f.write(clientLanguage+"\n")
                                f.write(clientFilename)
                            with open(app.config['SERVICES_FOLDER']+service+str(j_array[counter])+"/client/"+clientFilename, 'w') as f:
                                f.write(client)
                    if "about" in missing_files:
                        about = ""
                        try:
                            about = request.json['about']
                        except:
                            pass
                        if j_array[counter] == 0:
                            j_array[counter] = ""
                        # if url is docker index
                        if not "." in url or not "git" in url:
                            index_service = service.replace("/", "-")
                            if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])):
                                mkdir(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter]))
                            if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])+"/html"):
                                mkdir(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])+"/html")
                            with open(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])+"/"+SERVICE_DICT['about'], 'w') as f:
                                f.write(about)
                        elif url.rsplit('.', 1)[1] == "git": 
                            if not path.exists(app.config['SERVICES_FOLDER']+service+str(j_array[counter])+"/html"):
                                mkdir(app.config['SERVICES_FOLDER']+service+str(j_array[counter])+"/html")
                            with open(app.config['SERVICES_FOLDER']+service+str(j_array[counter])+"/"+SERVICE_DICT['about'], 'w') as f:
                                f.write(about)
                    if "body" in missing_files:
                        body = ""
                        try:
                            body = request.json['body']
                        except:
                            pass
                        if j_array[counter] == 0:
                            j_array[counter] = ""
                        # if url is docker index
                        if not "." in url or not "git" in url:
                            index_service = service.replace("/", "-")
                            if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])):
                                mkdir(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter]))
                            if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])+"/html"):
                                mkdir(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])+"/html")
                            with open(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])+"/"+SERVICE_DICT['body'], 'w') as f:
                                f.write(body)
                        elif url.rsplit('.', 1)[1] == "git": 
                            if not path.exists(app.config['SERVICES_FOLDER']+service+str(j_array[counter])+"/html"):
                                mkdir(app.config['SERVICES_FOLDER']+service+str(j_array[counter])+"/html")
                            with open(app.config['SERVICES_FOLDER']+service+str(j_array[counter])+"/"+SERVICE_DICT['body'], 'w') as f:
                                f.write(body)
                    if "link" in missing_files:
                        link = "#"
                        linkName = "None"
                        try:
                            link = request.json['link']
                            linkName = request.json['linkName']
                        except:
                            pass
                        if j_array[counter] == 0:
                            j_array[counter] = ""
                        # if url is docker index
                        if not "." in url or not "git" in url:
                            index_service = service.replace("/", "-")
                            if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])):
                                mkdir(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter]))
                            if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])+"/html"):
                                mkdir(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])+"/html")
                            with open(app.config['SERVICES_FOLDER']+index_service+str(j_array[counter])+"/"+SERVICE_DICT['link'], 'w') as f:
                                f.write(link+" "+linkName)
                        elif url.rsplit('.', 1)[1] == "git": 
                            if not path.exists(app.config['SERVICES_FOLDER']+service+str(j_array[counter])+"/html"):
                                mkdir(app.config['SERVICES_FOLDER']+service+str(j_array[counter])+"/html")
                            with open(app.config['SERVICES_FOLDER']+service+str(j_array[counter])+"/"+SERVICE_DICT['link'], 'w') as f:
                                f.write(link+" "+linkName)
                    counter += 1
            else:
                missing_files = request.json['missing_files']
                description = ""
                if "description" in missing_files:
                    try:
                        description = request.json['description']
                    except:
                        pass
                    if j == 0:
                        j = ""
                    # if url is docker index
                    if not "." in url or not "git" in url:
                        if description == "":
                            description = request.json['indexDesc']
                        index_service = services[0].replace("/", "-")
                        if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j)):
                            mkdir(app.config['SERVICES_FOLDER']+index_service+str(j))
                        with open(app.config['SERVICES_FOLDER']+index_service+str(j)+"/"+SERVICE_DICT['description'], 'w') as f:
                            f.write(description)
                    elif url.rsplit('.', 1)[1] == "git": 
                        with open(app.config['SERVICES_FOLDER']+(url.rsplit('/', 1)[1]).rsplit('.', 1)[0]+str(j)+"/"+SERVICE_DICT['description'], 'w') as f:
                            f.write(description)
                else:
                    if j == 0:
                        j = ""
                    # if url is docker index
                    if not "." in url or not "git" in url:
                        if description == "":
                            description = request.json['indexDesc']
                        index_service = services[0].replace("/", "-")
                        if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j)):
                            mkdir(app.config['SERVICES_FOLDER']+index_service+str(j))
                        with open(app.config['SERVICES_FOLDER']+index_service+str(j)+"/"+SERVICE_DICT['description'], 'w') as f:
                            f.write(description)
                if "client" in missing_files:
                    client = ""
                    clientLanguage = ""
                    clientFilename = "dummy.txt"
                    try:
                        client = request.json['client']
                        clientLanguage = request.json['clientLanguage']
                        clientFilename = request.json['clientFilename']
                    except:
                        pass
                    if j == 0:
                        j = ""
                    # if url is docker index
                    if not "." in url or not "git" in url:
                        index_service = services[0].replace("/", "-")
                        if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j)):
                            mkdir(app.config['SERVICES_FOLDER']+index_service+str(j))
                        if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j)+"/client"):
                            mkdir(app.config['SERVICES_FOLDER']+index_service+str(j)+"/client")
                        with open(app.config['SERVICES_FOLDER']+index_service+str(j)+"/"+SERVICE_DICT['client'], 'w') as f:
                            f.write(clientLanguage+"\n")
                            f.write(clientFilename)
                        with open(app.config['SERVICES_FOLDER']+index_service+str(j)+"/client/"+clientFilename, 'w') as f:
                            f.write(client)
                    elif url.rsplit('.', 1)[1] == "git": 
                        if not path.exists(app.config['SERVICES_FOLDER']+(url.rsplit('/', 1)[1]).rsplit('.', 1)[0]+str(j)+"/client"):
                            mkdir(app.config['SERVICES_FOLDER']+(url.rsplit('/', 1)[1]).rsplit('.', 1)[0]+str(j)+"/client")
                        with open(app.config['SERVICES_FOLDER']+(url.rsplit('/', 1)[1]).rsplit('.', 1)[0]+str(j)+"/"+SERVICE_DICT['client'], 'w') as f:
                            f.write(clientLanguage+"\n")
                            f.write(clientFilename)
                        with open(app.config['SERVICES_FOLDER']+(url.rsplit('/', 1)[1]).rsplit('.', 1)[0]+str(j)+"/client/"+clientFilename, 'w') as f:
                            f.write(client)
                if "about" in missing_files:
                    about = ""
                    try:
                        about = request.json['about']
                    except:
                        pass
                    if j == 0:
                        j = ""
                    # if url is docker index
                    if not "." in url or not "git" in url:
                        index_service = services[0].replace("/", "-")
                        if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j)):
                            mkdir(app.config['SERVICES_FOLDER']+index_service+str(j))
                        if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j)+"/html"):
                            mkdir(app.config['SERVICES_FOLDER']+index_service+str(j)+"/html")
                        with open(app.config['SERVICES_FOLDER']+index_service+str(j)+"/"+SERVICE_DICT['about'], 'w') as f:
                            f.write(about)
                    elif url.rsplit('.', 1)[1] == "git": 
                        if not path.exists(app.config['SERVICES_FOLDER']+(url.rsplit('/', 1)[1]).rsplit('.', 1)[0]+str(j)+"/html"):
                            mkdir(app.config['SERVICES_FOLDER']+(url.rsplit('/', 1)[1]).rsplit('.', 1)[0]+str(j)+"/html")
                        with open(app.config['SERVICES_FOLDER']+(url.rsplit('/', 1)[1]).rsplit('.', 1)[0]+str(j)+"/"+SERVICE_DICT['about'], 'w') as f:
                            f.write(about)
                if "body" in missing_files:
                    body = ""
                    try:
                        body = request.json['body']
                    except:
                        pass
                    if j == 0:
                        j = ""
                    # if url is docker index
                    if not "." in url or not "git" in url:
                        index_service = services[0].replace("/", "-")
                        if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j)):
                            mkdir(app.config['SERVICES_FOLDER']+index_service+str(j))
                        if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j)+"/html"):
                            mkdir(app.config['SERVICES_FOLDER']+index_service+str(j)+"/html")
                        with open(app.config['SERVICES_FOLDER']+index_service+str(j)+"/"+SERVICE_DICT['body'], 'w') as f:
                            f.write(body)
                    elif url.rsplit('.', 1)[1] == "git": 
                        if not path.exists(app.config['SERVICES_FOLDER']+(url.rsplit('/', 1)[1]).rsplit('.', 1)[0]+str(j)+"/html"):
                            mkdir(app.config['SERVICES_FOLDER']+(url.rsplit('/', 1)[1]).rsplit('.', 1)[0]+str(j)+"/html")
                        with open(app.config['SERVICES_FOLDER']+(url.rsplit('/', 1)[1]).rsplit('.', 1)[0]+str(j)+"/"+SERVICE_DICT['body'], 'w') as f:
                            f.write(body)
                if "link" in missing_files:
                    link = "#"
                    linkName = "None"
                    try:
                        link = request.json['link']
                        linkName = request.json['linkName']
                    except:
                        pass
                    if j == 0:
                        j = ""
                    # if url is docker index
                    if not "." in url or not "git" in url:
                        index_service = services[0].replace("/", "-")
                        if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j)):
                            mkdir(app.config['SERVICES_FOLDER']+index_service+str(j))
                        if not path.exists(app.config['SERVICES_FOLDER']+index_service+str(j)+"/html"):
                            mkdir(app.config['SERVICES_FOLDER']+index_service+str(j)+"/html")
                        with open(app.config['SERVICES_FOLDER']+index_service+str(j)+"/"+SERVICE_DICT['link'], 'w') as f:
                            f.write(link+" "+linkName)
                    elif url.rsplit('.', 1)[1] == "git": 
                        if not path.exists(app.config['SERVICES_FOLDER']+(url.rsplit('/', 1)[1]).rsplit('.', 1)[0]+str(j)+"/html"):
                            mkdir(app.config['SERVICES_FOLDER']+(url.rsplit('/', 1)[1]).rsplit('.', 1)[0]+str(j)+"/html")
                        with open(app.config['SERVICES_FOLDER']+(url.rsplit('/', 1)[1]).rsplit('.', 1)[0]+str(j)+"/"+SERVICE_DICT['link'], 'w') as f:
                            f.write(link+" "+linkName)
    except:
        pass
    return jsonify(url=DOMAIN)

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/robot.txt')
def robot():
    return render_template("robot.html")

if __name__ == '__main__':
    app.run(host="0.0.0.0")
