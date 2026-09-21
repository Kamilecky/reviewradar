from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


class RegistrationForm(UserCreationForm):
    """UserCreationForm plus a required, unique email (needed for password reset by email)."""

    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    def clean_email(self):
        """Reject the email if any existing User already has it."""
        email = self.cleaned_data["email"]
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("Konto z tym adresem e-mail już istnieje.")
        return email


class EmailChangeForm(forms.Form):
    """Lets a logged-in user change their own email address (no Django built-in for this)."""

    email = forms.EmailField(required=True)

    def __init__(self, *args, user=None, **kwargs):
        """Store the current user so clean_email can exclude them from the uniqueness check."""
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_email(self):
        """Reject the email if it belongs to a *different* existing User."""
        email = self.cleaned_data["email"]
        if User.objects.exclude(pk=self.user.pk).filter(email=email).exists():
            raise forms.ValidationError("Ten adres e-mail jest już używany przez inne konto.")
        return email
