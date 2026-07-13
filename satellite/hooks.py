app_name = "satellite"
app_title = "Satellite"
app_publisher = "Frappe"
app_description = "Service layer for Atlas: routing, gateway, mesh, bench/site behind the VM lifecycle seam (spec/28)"
app_email = "aditya@frappe.io"
app_license = "agpl-3.0"

# Apps
# ------------------

# Satellite is the service layer that attaches to Atlas's generic VM lifecycle
# through the seam Atlas core exposes (spec/28-core-service-boundary.md). It is a
# controller + setup scripts only — every host/guest effect runs through the infra
# methods Atlas exposes (atlas.atlas.vm_services), never SSH/providers here.
required_apps = ["atlas"]

# 3A — the lifecycle registry. Atlas core reads this hook and calls each service at
# its VM-lifecycle hook points (validate / provision_variables / on_provision /
# on_status_change / teardown). Core never imports satellite; this hook is the only
# link. Declared order is the order core runs them.
atlas_vm_services = [
	"satellite.services.mesh.MeshService",
]

# 3B — register satellite's Task-script tree with Atlas's runner. Atlas stages and
# runs these on a host/guest on satellite's behalf; satellite opens no SSH itself.
# "<app>:<relative/dir>" is resolved against the app's module path.
atlas_script_directories = [
	"satellite:scripts",
]

# The service's own custom fields on Atlas's Virtual Machine, shipped as fixtures
# (never by hand-editing virtual_machine.json). `satellite_managed` is the flag each
# service's applies_to() reads; owned here, not in core.
fixtures = [
	{
		"dt": "Custom Field",
		"filters": [["name", "in", ["Virtual Machine-satellite_managed"]]],
	},
]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "satellite",
# 		"logo": "/assets/satellite/logo.png",
# 		"title": "Satellite",
# 		"route": "/satellite",
# 		"has_permission": "satellite.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/satellite/css/satellite.css"
# app_include_js = "/assets/satellite/js/satellite.js"

# include js, css files in header of web template
# web_include_css = "/assets/satellite/css/satellite.css"
# web_include_js = "/assets/satellite/js/satellite.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "satellite/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "satellite/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "satellite.utils.jinja_methods",
# 	"filters": "satellite.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "satellite.install.before_install"
# after_install = "satellite.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "satellite.uninstall.before_uninstall"
# after_uninstall = "satellite.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "satellite.utils.before_app_install"
# after_app_install = "satellite.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "satellite.utils.before_app_uninstall"
# after_app_uninstall = "satellite.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "satellite.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "satellite.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"satellite.tasks.all"
# 	],
# 	"daily": [
# 		"satellite.tasks.daily"
# 	],
# 	"hourly": [
# 		"satellite.tasks.hourly"
# 	],
# 	"weekly": [
# 		"satellite.tasks.weekly"
# 	],
# 	"monthly": [
# 		"satellite.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "satellite.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "satellite.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "satellite.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "satellite.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["satellite.utils.before_request"]
# after_request = ["satellite.utils.after_request"]

# Job Events
# ----------
# before_job = ["satellite.utils.before_job"]
# after_job = ["satellite.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"satellite.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
export_python_type_annotations = True

# Require all whitelisted methods to have type annotations
require_type_annotated_api_methods = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

