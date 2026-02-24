from django.shortcuts import redirect
from django.views.generic import TemplateView
from django.contrib import messages
from osf.models import RegistrationProvider, OSFUser, CollectionProvider, NotificationType
from website.settings import DOMAIN
from django.core.mail import send_mail


class AddAdminOrModerator(TemplateView):
    permission_required = 'osf.change_registrationprovider'
    template_name = 'registration_providers/edit_moderators.html'
    provider_class = RegistrationProvider
    url_namespace = 'registration_providers'
    raise_exception = True

    def get_context_data(self, **kwargs):
        provider = self.provider_class.objects.get(id=self.kwargs['provider_id'])
        context = super().get_context_data(**kwargs)
        context['provider'] = provider
        context['moderators'] = provider.get_group('moderator').user_set.all()
        context['admins'] = provider.get_group('admin').user_set.all()
        return context

    def post(self, request, *args, **kwargs):
        provider = self.provider_class.objects.get(id=self.kwargs['provider_id'])
        data = dict(request.POST)
        del data['csrfmiddlewaretoken']  # just to remove the key from the form dict

        target_user = OSFUser.load(data['add-moderators-form'][0])
        if target_user is None:
            messages.error(request, f'User for guid: {data["add-moderators-form"][0]} could not be found')
            return redirect(f'{self.url_namespace}:add_admin_or_moderator', provider_id=provider.id)

        if target_user.has_groups(provider.group_names):
            messages.error(request, f'User with guid: {data["add-moderators-form"][0]} is already a moderator or admin')
            return redirect(f'{self.url_namespace}:add_admin_or_moderator', provider_id=provider.id)
        context = {}
        context['user_fullname'] = target_user.fullname
        context['referrer_fullname'] = self.request.user.username
        context['provider_name'] = provider.name

        if 'admin' in data:
            provider.add_to_group(target_user, 'admin')
            target_type = 'admin'
            context['is_admin'] = True
        else:
            provider.add_to_group(target_user, 'moderator')
            target_type = 'moderator'
            context['is_admin'] = False

        context['provider_name'] = provider.name
        context['provider__id'] = provider._id
        context['is_reviews_moderator_notification'] = True

        if isinstance(provider, RegistrationProvider):
            provider_type_word = 'registries'
            context['notification_settings_url'] = f'{DOMAIN}registries/{provider._id}/moderation/settings'
        elif isinstance(provider, CollectionProvider):
            provider_type_word = 'collections'
            context['notification_settings_url'] = f'{DOMAIN}registries/{provider._id}/moderation/settings'
        else:
            provider_type_word = 'preprints'
            context['notification_settings_url'] = f'{DOMAIN}preprints/{provider._id}/moderation/notifications'

        context['provider_url'] = f'{provider.domain or DOMAIN}{provider_type_word}/{(provider._id if not provider.domain else '').strip('/')}'
        messages.success(request, f'The following {target_type} was successfully added: {target_user.fullname} ({target_user.username})')
        notification_type = NotificationType.Type.PROVIDER_MODERATOR_ADDED
        notification_type.instance.emit(
            user=target_user,
            event_context=context,
        )
        return redirect(f'{self.url_namespace}:add_admin_or_moderator', provider_id=provider.id)


class RemoveAdminsAndModerators(TemplateView):
    permission_required = 'osf.change_registrationprovider'
    template_name = 'registration_providers/edit_moderators.html'
    provider_class = RegistrationProvider
    url_namespace = 'registration_providers'
    raise_exception = True

    def get_context_data(self, **kwargs):
        provider = self.provider_class.objects.get(id=self.kwargs['provider_id'])
        context = super().get_context_data(**kwargs)
        context['provider'] = provider
        context['moderators'] = provider.get_group('moderator').user_set.all()
        context['admins'] = provider.get_group('admin').user_set.all()
        return context

    def post(self, request, *args, **kwargs):
        provider = self.provider_class.objects.get(id=self.kwargs['provider_id'])
        data = dict(request.POST)
        del data['csrfmiddlewaretoken']  # just to remove the key from the form dict

        to_be_removed = list(data.keys())
        removed_admins = [admin.replace('Admin-', '') for admin in to_be_removed if 'Admin-' in admin]
        removed_moderators = [moderator.replace('Moderator-', '') for moderator in to_be_removed if 'Moderator-' in moderator]
        moderators = OSFUser.objects.filter(id__in=removed_moderators)
        admins = OSFUser.objects.filter(id__in=removed_admins)
        provider.get_group('moderator').user_set.remove(*moderators)
        provider.get_group('admin').user_set.remove(*admins)

        if moderators:
            moderator_names = ' ,'.join(moderators.values_list('fullname', flat=True))
            messages.success(request, f'The following moderators were successfully removed: {moderator_names}')

        if admins:
            admin_names = ' ,'.join(admins.values_list('fullname', flat=True))
            messages.success(request, f'The following admins were successfully removed: {admin_names}')

        return redirect(f'{self.url_namespace}:add_admin_or_moderator', provider_id=provider.id)

import csv
import codecs
from osf.models import Registration

class BulkChangeRegistrationProvider(TemplateView):
    permission_required = 'osf.change_registrationprovider'
    template_name = 'registration_providers/bulk_change_registration_provider.html'
    provider_class = RegistrationProvider
    url_namespace = 'registration_providers'
    raise_exception = True

    def get_context_data(self, **kwargs):
        provider = self.provider_class.objects.get(id=self.kwargs['provider_id'])
        context = super().get_context_data(**kwargs)
        context['provider'] = provider
        context['moderators'] = provider.get_group('moderator').user_set.all()
        context['admins'] = provider.get_group('admin').user_set.all()
        return context

    def get_guids_from_csv(self, file):
        guids = []
        for row in csv.DictReader(codecs.iterdecode(file.file, 'utf-8'), delimiter=','):
            for key, value in row.items():
                if key == 'guid':
                    guids.append(value)
        return guids

    def bulk_update_provider(self, guids, provider_id):

        # Fetch registrations based on GUIDs
        registrations = Registration.objects.filter(guids___id__in=guids)

        # Determine GUIDs that were not found
        found_guids = set(registrations.values_list('guids___id', flat=True))
        missing_guids = set(guids) - found_guids

        if registrations:
            # Update the provider ID for each registration
            for registration in registrations:
                registration.provider_id = provider_id

            # Bulk update the registrations
            try:
                Registration.objects.bulk_update(registrations, ['provider_id'])
                success_count = len(registrations)
            except Exception as e:
                success_count = 0
                error_message = str(e)
            else:
                error_message = None

            # Prepare the email report
            subject = 'Bulk Update Provider Report'
            message = f"Successfully updated provider ID for {success_count} registrations.\n"
            if missing_guids:
                message += f"The following GUIDs were not found: {', '.join(missing_guids)}\n"
            if error_message:
                message += f"An error occurred during the update: {error_message}\n"

            # Send the email
            send_mail(
                subject,
                message,
                'noreply@example.com',  # Replace with the sender's email address
                ['cos_employee@example.com'],  # Replace with the recipient's email address
            )

            print(message)

    def post(self, request, *args, **kwargs):
        import pydevd_pycharm
        pydevd_pycharm.settrace('host.docker.internal', port=1234, stdoutToServer=True, stderrToServer=True)
        provider_id = self.provider_class.objects.get(id=self.kwargs['provider_id']).id
        csv_file = request.FILES.get('csv')
        guids = self.get_guids_from_csv(csv_file)
        self.bulk_update_provider(guids, provider_id)

        # data = dict(request.POST)
        # del data['csrfmiddlewaretoken']  # just to remove the key from the form dict
        #
        # to_be_removed = list(data.keys())
        # removed_admins = [admin.replace('Admin-', '') for admin in to_be_removed if 'Admin-' in admin]
        # removed_moderators = [moderator.replace('Moderator-', '') for moderator in to_be_removed if 'Moderator-' in moderator]
        # moderators = OSFUser.objects.filter(id__in=removed_moderators)
        # admins = OSFUser.objects.filter(id__in=removed_admins)
        # provider.get_group('moderator').user_set.remove(*moderators)
        # provider.get_group('admin').user_set.remove(*admins)
        #
        # if moderators:
        #     moderator_names = ' ,'.join(moderators.values_list('fullname', flat=True))
        #     messages.success(request, f'The following moderators were successfully removed: {moderator_names}')
        #
        # if admins:
        #     admin_names = ' ,'.join(admins.values_list('fullname', flat=True))
        #     messages.success(request, f'The following admins were successfully removed: {admin_names}')

        return redirect(f'{self.url_namespace}:detail', registration_provider_id=provider_id)
