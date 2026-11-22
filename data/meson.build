desktop_file = i18n.merge_file(
  input: 'io.github.myrnop.BeamDrop.desktop.in',
  output: 'io.github.myrnop.BeamDrop.desktop',
  type: 'desktop',
  po_dir: '../po',
  install: true,
  install_dir: join_paths(get_option('datadir'), 'applications')
)

desktop_utils = find_program('desktop-file-validate', required: false)
if desktop_utils.found()
  test('Validate desktop file', desktop_utils, args: [desktop_file])
endif

appstream_file = i18n.merge_file(
  input: 'io.github.myrnop.BeamDrop.metainfo.xml.in',
  output: 'io.github.myrnop.BeamDrop.metainfo.xml',
  po_dir: '../po',
  install: true,
  install_dir: join_paths(get_option('datadir'), 'metainfo')
)

appstreamcli = find_program('appstreamcli', required: false)
if appstreamcli.found()
  test('Validate appstream file', appstreamcli, args: ['validate', '--no-net', appstream_file])
endif

install_data(
  'icons/hicolor/scalable/apps/io.github.myrnop.BeamDrop.svg',
  install_dir: join_paths(get_option('datadir'), 'icons/hicolor/scalable/apps')
)

install_data(
  'icons/hicolor/symbolic/apps/io.github.myrnop.BeamDrop-symbolic.svg',
  install_dir: join_paths(get_option('datadir'), 'icons/hicolor/symbolic/apps')
)