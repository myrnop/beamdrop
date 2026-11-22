conf = configuration_data()
conf.set('PYTHON', py_installation.full_path())
conf.set('VERSION', meson.project_version())
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'beamdrop.in',
  output: 'beamdrop',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x'
)

py_installation.install_sources(
  [
    '__init__.py',
    'main.py',
    'window.py',
    'server.py',
    'qr_utils.py',
  ],
  subdir: 'beamdrop'
)