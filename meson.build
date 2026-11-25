project('beamdrop',
  version: '0.2.0',
  meson_version: '>= 0.62.0',
)

i18n = import('i18n')
gnome = import('gnome')
python = import('python')

py_installation = python.find_installation('python3')
pkgdatadir = join_paths(get_option('prefix'), get_option('datadir'), meson.project_name())

subdir('data')
subdir('src')

gnome.post_install(
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)