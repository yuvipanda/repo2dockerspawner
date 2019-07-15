from dockerspawner import DockerSpawner
from repo2docker.app import Repo2Docker
from concurrent.futures import ThreadPoolExecutor
from tornado.ioloop import IOLoop
import asyncio
from escapism import escape
from traitlets import Unicode
import docker.errors

async def subprocess_output(cmd, **kwargs):
    """
    Run cmd until completion & return stdout, stderr

    Convenience method to start and run a process
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **kwargs)

    stdout, stderr = await proc.communicate() 

    return stdout.decode(), stderr.decode()

async def resolve_ref(repo_url, requested_ref):
    """
    Return resolved commit hash for branch / tag.

    Return ref unmodified if branch / tag isn't found
    """
    stdout, stderr = await subprocess_output(
        ['git', 'ls-remote', repo_url]
    )
    # ls-remote output looks like this:
    # <hash>\t<ref>\n
    # <hash>\t<ref>\n
    # Since our ref can be a tag (so refs/tags/<ref>) or branch
    # (so refs/head/<ref>), we get all refs and check if either
    # exists
    all_refs = [l.split('\t') for l in stdout.strip().split('\n')]
    for hash, ref in all_refs:
        if requested_ref in hash or requested_ref in ref:
            return hash

    if stdout:
        return stdout.split()[0]
    return ref

class Repo2DockerSpawner(DockerSpawner):
    # ThreadPool for talking to r2d
    _r2d_executor = None

    def options_form(self, spawner):
        return """
        <label for="repo">Github repository to clone</label>
        <input name="repo" placeholder="e.g. pytudes"></input>
        <label for="ref">Reference to checkout</label>
        <input name="ref" placeholder="master"></input>
        """.format(
            ref='master'
        )
    
    def options_from_form(self, formdata):
        options = {}
        options['repo'] = ''
        options['ref'] = ''
        
        repo_s = formdata.get('repo', [''])[0].strip()
        if repo_s:
            options['repo'] = repo_s
            
        ref_s = formdata.get('ref', [''])[0].strip()
        if ref_s:
            options['ref'] = ref_s
        return options
    
    def run_in_executor(self, func, *args):
        # FIXME: This shouldn't be used for anything other than r2d.build
        cls = self.__class__
        if cls._r2d_executor is None:
            # FIXME: Figure out what is correct number here
            cls._r2d_executor = ThreadPoolExecutor(1)
        return IOLoop.current().run_in_executor(cls._r2d_executor, func, *args)


    start_timeout = 20 * 60

    # We don't want stopped containers hanging around
    remove = True

    # Default r2d images start jupyter notebook, not singleuser
    cmd = ['jupyterhub-singleuser']

    repo = Unicode(
        None,
        allow_none=True,
        config=True,
        help="""
        Repository to pass to repo2docker.

        Should not be None
        """
    )

    ref = Unicode(
        'master',
        config=True,
        help="""
        Ref to pass to repo2docker.
        """
    )

    async def inspect_image(self, image_spec):
        """
        Return docker image info if image exists, None otherwise
        """
        try:
            loop = IOLoop.current()
            # FIXME: Can't see to use self.docker here, fails with
            # `object Future can't be used in 'await' expression`.
            # So we reach into self.executor and self.client, which makes me nervous
            image_info = await loop.run_in_executor(self.executor, self.client.inspect_image, image_spec)
            return image_info
        except docker.errors.ImageNotFound:
            return None

    async def start(self):
        repo_domain = 'https://github.com/mistamun/'
        repo = self.user_options['repo']
        resolved_ref = await resolve_ref(f'{repo_domain}{repo}', self.user_options['ref'])
        repo_escaped = escape(f'{repo_domain}{repo}', escape_char='-').lower()
        image_spec = f'r2dspawner-{repo_escaped}:{resolved_ref}'
        
        self.log.debug(f'Repo: {repo_domain}{repo}')
        self.log.debug(f'Ref: {self.user_options["ref"]}')
        self.log.debug(f'Resolved ref: {resolved_ref}')
        self.log.debug(f'Repo escaped: {repo_escaped}')
        self.log.debug(f'Image spec: {image_spec}')
        
        image_info = await self.inspect_image(image_spec)
        if not image_info:
            self.log.info(f'Image {image_spec} not present, building...')
            r2d = Repo2Docker()
            r2d.repo = f'{repo_domain}{repo}'
            r2d.ref = resolved_ref
            r2d.user_id = 1000
            r2d.user_name = 'cfd'

            r2d.output_image_spec = image_spec
            r2d.initialize()

            await self.run_in_executor(r2d.build)


        # HACK: DockerSpawner (and traitlets) don't seem to realize we're setting 'cmd',
        # and refuse to use our custom command. Explicitly set this variable for
        # now.
        self._user_set_cmd = True

        self.log.info(f'Launching with image {image_spec} for {self.user.name}')
        self.image = image_spec

        return await super().start()
