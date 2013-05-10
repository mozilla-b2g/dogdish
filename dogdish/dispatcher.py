#!/usr/bin/env python

"""
dogdish
https://bugzilla.mozilla.org/show_bug.cgi?id=800118
"""

import fnmatch
import hashlib
import os
import sys
from urlparse import urlparse
from webob import Request
from webob import Response, exc
from ConfigParser import RawConfigParser as ConfigParser

here = os.path.dirname(os.path.abspath(__file__))

### models

class Application(object):
    """class for storing application.ini data"""

    def __init__(self, filename):
        """
        - filename : path to an application.ini file
        """
        config = ConfigParser()
        config.read(filename)
        self.build_id = config.get('App', 'BuildID')
        self.version = config.get('App', 'Version')


class Update(object):
    """class representing a .mar update file"""

    prefix = 'b2g_update_'
    suffix = '.mar'

    @classmethod
    def updates(cls, directory):
        """returns the updates in a directory"""

        contents = [i for i in os.listdir(directory)
                    if i.startswith(cls.prefix) and i.endswith(cls.suffix)]
        contents = set(contents)
        return contents

    def __init__(self, directory, filename):
        self.directory = directory
        self.filename = filename
        self.path = os.path.join(directory, filename)
        self.stamp = filename[len(self.prefix):-len(self.suffix)]
        self.modifiedTime = os.path.getmtime(self.path)
        self.size = os.path.getsize(self.path)

        # cached properties
        self._application = None
        self._hash = None

    def application(self):
        """
        returns the path to the application.ini
        associated with this update
        """

        if not self._application:
            application_ini = 'application_%s.ini' % self.stamp
            application_ini = os.path.join(self.directory, application_ini)
            assert os.path.exists(application_ini)
            self._application = Application(application_ini)
        return self._application

    def hash(self):
        if not self._hash:
            # compute the hash
            with file(self.path) as f:
                self._hash = hashlib.sha512(f.read()).hexdigest()
        return self._hash

class UpdateStable(Update):
    prefix = 'b2g_stable_update_'


### request handlers

class Handler(object):
    """abstract handler object for a request"""

    def __init__(self, app, request):
        self.app = app
        self.request = request
        self.application_path = urlparse(request.application_url)[2]

    def link(self, path=(), permanant=False):
        if isinstance(path, basestring):
            path = [ path ]
        path = [ i.strip('/') for i in path ]
        if permanant:
            application_url = [ self.request.application_url ]
        else:
            application_url = [ self.application_path ]
        path = application_url + path
        return '/'.join(path)

    def redirect(self, location):
        raise exc.HTTPSeeOther(location=location)

class Get(Handler):
    """handle GET requests"""

    # template for response body
    body = """<?xml version="1.0"?>
<updates>
  <update type="minor" appVersion="%(version)s" version="%(version)s" extensionVersion="%(version)s" buildID="%(build_id)s" licenseURL="http://www.mozilla.com/test/sample-eula.html" detailsURL="http://www.mozilla.com/test/sample-details.html">
    <patch type="complete" URL="http://update.boot2gecko.org/%(path)s/%(update)s%(query)s" hashFunction="SHA512" hashValue="%(hash)s" size="%(size)s"/>
  </update>
</updates>"""

    ### methods for request handler

    @classmethod
    def match(cls, request):
        return request.method == 'GET'

    def __call__(self):

        body = self.body
        query = {}
        dogfood_id = self.request.GET.get('dogfood_id')
        if dogfood_id:
            query['dogfooding_prerelease_id'] = dogfood_id
        current_update = self.app.current_update
        application = current_update.application()
        query['build_id'] = application.build_id
        query['version'] = application.version

        # build query string
        if query:
            query = '?' + '&amp;'.join(['%s=%s' % (key, value) for key, value in query.items()])
        else:
            query = ''


        # template variables
        variables = dict(update=current_update.filename,
                         size=current_update.size,
                         version=application.version,
                         hash=current_update.hash(),
                         path=self.app.path,
                         build_id=application.build_id,
                         query=query)

        return Response(content_type='text/xml',
                        body=body % variables)

class Dispatcher(object):
    """web application"""


    ### class level variables
    defaults = {'directory': here,
                'path': None,
                'update_class': Update}

    def __init__(self, **kw):

        # set defaults
        for key in self.defaults:
            setattr(self, key, kw.get(key, self.defaults[key]))
        self.handlers = [ Get ]

        # path
        if not self.path:
            self.path = os.path.split(self.directory.strip(os.path.sep))[-1]

        # cache
        self.updates = {}
        self.current_update = None

        # scan directory
        self.scan()
        assert self.current_update, "No updates found in %s" % self.directory

    def __call__(self, environ, start_response):
        """WSGI application"""

        request = Request(environ)
        self.scan()
        for h in self.handlers:
            if h.match(request):
                handler = h(self, request)
                res = handler()
                break
        else:
            res = exc.HTTPNotFound()

        return res(environ, start_response)

    def scan(self):
        """
        scan the directory for updates
        returns True if new updates are found, False otherwise
        """

        # check for new updates
        contents = self.update_class.updates(self.directory)
        for update in contents:
            self.updates[update] = self.update_class(self.directory, update)
            if self.current_update:
                if self.updates[update].modifiedTime > self.current_update.modifiedTime:
                    self.current_update = self.updates[update]
            else:
                self.current_update = self.updates[update]

        # TODO: could remove old files from the cache if not found in contents

        return True

def main(args=sys.argv[1:]):
    """CLI entry point"""

    # imports for CLI
    import optparse
    from wsgiref import simple_server

    # parse CLI options
    parser = optparse.OptionParser()
    parser.add_option('-p', '--port', dest='port',
                      default=8080, type='int',
                      help="port to serve on")
    parser.add_option('-d', '--directory', dest='directory',
                      default=os.getcwd(),
                      help="directory of update files")
    options, args = parser.parse_args()

    # create the app
    app = Dispatcher(directory=options.directory)

    # serve the app
    print "http://localhost:%s/" % options.port
    server = simple_server.make_server(host='0.0.0.0', port=options.port, app=app)
    server.serve_forever()

if __name__ == '__main__':
    main()
