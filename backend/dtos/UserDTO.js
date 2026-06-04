export class UserDTO {
    constructor(user) {
        this._id = user._id;
        this.username = user.username;
        this.roles = user.roles;

        this.createdAt = user.createdAt
        this.updatedAt = user.updatedAt
    }

    mainInfo(){
        return {
            _id: this._id,
            username: this.username,
        }
    }
}

